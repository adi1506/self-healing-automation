"""Replay-time healer.

When `find_element_by_fingerprint` exhausts every stored locator strategy,
this module attempts to relocate the element by scoring every interactive
element on the current page against the stored fingerprint's attributes.

The matcher is locator-blind: by the time we run, all five stored locators
have already missed against the live DOM, so structural identifiers
(id, name, css_path, xpath) are off the table as identity signals. We
score on the descriptive attributes the recorder collected — label,
placeholder, autocomplete, type, role, html5 constraints, aria_label.

Design notes and threshold rationale: see REPLAY_HEALING_DESIGN.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import urlsplit

from playwright.async_api import Page

from core.recording import ElementFingerprint


# --- Decision thresholds --------------------------------------------------
SCORE_THRESHOLD = 0.80   # top match must clear this to auto-heal
GRAY_LOW = 0.55          # below this, no AI consultation either
MARGIN_REQ = 0.10        # top must beat runner-up by at least this
AUTO_PERSIST_THRESHOLD = 0.90   # stricter bar for opt-in auto-write-back


# --- Feature weights (sum to 1.0 — see design doc §2.4) ------------------
_WEIGHTS = {
    "autocomplete": 0.25,
    "nearest_label_text": 0.20,
    "name": 0.20,
    "id": 0.10,
    "tag_type": 0.10,
    "placeholder": 0.05,
    "aria_label": 0.05,
    "pattern": 0.03,
    "role": 0.02,
}


# --- Result types --------------------------------------------------------
@dataclass
class CandidateRef:
    """A scored candidate preserved so the run-report can offer 'retry
    with second-best' on failure. The chosen heal is top_k_candidates[0]."""
    primary_locator: dict
    fallback_locators: list[dict]
    attributes: dict
    score: float


@dataclass
class HealDecision:
    """Outcome of a heal attempt against the live page.

    `method` is one of:
      - "auto"          heuristic match cleared SCORE_THRESHOLD + MARGIN_REQ
      - "ai-confirmed"  gray-zone candidate confirmed by AIMatcher
      - "unresolved"    no candidate confident enough; step will fail
    """
    method: str
    matched_candidate: Optional[ElementFingerprint] = None
    confidence: float = 0.0
    runner_up_score: float = 0.0
    matched_by: list[str] = field(default_factory=list)
    new_primary_locator: Optional[dict] = None
    new_fallback_locators: list[dict] = field(default_factory=list)
    diagnostics: str = ""
    top_k_candidates: list[CandidateRef] = field(default_factory=list)

    @classmethod
    def unresolved(
        cls,
        diagnostics: str,
        runner_up_score: float = 0.0,
        top_k_candidates: Optional[list] = None,
    ) -> "HealDecision":
        return cls(
            method="unresolved",
            diagnostics=diagnostics,
            runner_up_score=runner_up_score,
            top_k_candidates=list(top_k_candidates or []),
        )


# --- Action / URL guards -------------------------------------------------
def is_action_compatible(action: str, fp: ElementFingerprint) -> bool:
    """Return True if `action` can run against an element with this fingerprint.

    A stored 'fill' step cannot be healed onto a `<button>` no matter how
    well its label matches — that's a recording-rewrite case, not a heal.
    Surface those explicitly rather than silently mismatching.
    """
    tag = (fp.attributes.get("tag") or "").lower()
    typ = (fp.attributes.get("type") or "").lower()
    role = (fp.attributes.get("role") or "").lower()

    if action == "fill":
        if tag == "textarea":
            return True
        if tag == "input" and typ not in ("checkbox", "radio", "submit", "button", "file", "image"):
            return True
        if role == "textbox":
            return True
        return False
    if action == "select":
        return tag == "select" or role == "combobox"
    if action in ("check", "uncheck"):
        if tag == "input" and typ in ("checkbox", "radio"):
            return True
        return role in ("checkbox", "radio")
    if action in ("click", "submit"):
        if tag in ("button", "a"):
            return True
        if tag == "input" and typ in ("submit", "button"):
            return True
        return role in ("button", "link")
    if action == "press":
        # `press` can target any focusable; be permissive.
        return True
    # Unknown action — default permissive; the step will fail downstream
    # with a clearer error than ours.
    return True


def urls_compatible(stored_url: str, current_url: str) -> bool:
    """Heuristic: same host + same path is the same page.

    Query string and fragment are ignored (forms often append `?step=2`,
    `#section`, etc. without representing a different page). An empty
    stored URL disables the check (older recordings, or fingerprints not
    bound to a specific page).
    """
    if not stored_url:
        return True
    try:
        a = urlsplit(stored_url)
        b = urlsplit(current_url)
    except Exception:
        return True
    if a.scheme and b.scheme and a.scheme != b.scheme:
        # http -> https on the same host+path is still the same page
        pass
    return (a.hostname or "") == (b.hostname or "") and (a.path or "/") == (b.path or "/")


# --- Scoring -------------------------------------------------------------
def _str_sim(a: str, b: str) -> tuple[float, bool]:
    """Return (similarity, skip). Skip if either side is empty.

    Rationale: empty-on-one-side is most often "the UI added/removed an
    attribute," not "wrong element." Penalising would punish heals that
    are actually correct. We err toward neutrality and let other features
    carry the discrimination.
    """
    a = (a or "").lower().strip()
    b = (b or "").lower().strip()
    if not a or not b:
        return 0.0, True
    if a == b:
        return 1.0, False
    return SequenceMatcher(None, a, b).ratio(), False


def _exact_or_skip(a: str, b: str) -> tuple[float, bool]:
    """Categorical equality. Skip if either side is empty."""
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return 0.0, True
    return (1.0 if a == b else 0.0), False


def score_candidate(stored: ElementFingerprint, candidate: ElementFingerprint) -> tuple[float, list[str]]:
    """Return (normalized_score, matched_features).

    Both-empty features contribute neither to the numerator nor the
    denominator — that prevents low-signal recordings from being unfairly
    penalised, e.g. a form with no autocomplete attrs anywhere.

    A feature is considered "matched" (and recorded for UI explanation)
    when its per-feature score >= 0.8.
    """
    s_attrs = stored.attributes
    c_attrs = candidate.attributes

    feature_scores: dict[str, float] = {}

    # Categorical features
    for key in ("autocomplete", "role"):
        sc, empty = _exact_or_skip(s_attrs.get(key, ""), c_attrs.get(key, ""))
        if not empty:
            feature_scores[key] = sc

    s_pat = (s_attrs.get("html5_constraints") or {}).get("pattern", "")
    c_pat = (c_attrs.get("html5_constraints") or {}).get("pattern", "")
    sc, empty = _exact_or_skip(s_pat, c_pat)
    if not empty:
        feature_scores["pattern"] = sc

    # String-similarity features
    for key in ("nearest_label_text", "name", "id", "placeholder", "aria_label"):
        sc, empty = _str_sim(s_attrs.get(key, ""), c_attrs.get(key, ""))
        if not empty:
            feature_scores[key] = sc

    # Tag + type combined — always present (every element has a tag)
    s_tag = (s_attrs.get("tag") or "").lower()
    c_tag = (c_attrs.get("tag") or "").lower()
    s_typ = (s_attrs.get("type") or "").lower()
    c_typ = (c_attrs.get("type") or "").lower()
    if s_tag == c_tag and s_typ == c_typ:
        feature_scores["tag_type"] = 1.0
    elif s_tag == c_tag:
        feature_scores["tag_type"] = 0.5
    else:
        feature_scores["tag_type"] = 0.0

    # Weighted normalised average over present features only
    total_weight = sum(_WEIGHTS[k] for k in feature_scores)
    if total_weight == 0:
        return 0.0, []
    weighted_sum = sum(_WEIGHTS[k] * v for k, v in feature_scores.items())
    score = weighted_sum / total_weight

    matched = [k for k, v in feature_scores.items() if v >= 0.8]
    return score, matched


# --- Candidate scope filter -----------------------------------------------
def _filter_by_landmark(stored: ElementFingerprint, candidates: list[ElementFingerprint]) -> list[ElementFingerprint]:
    """If the stored fingerprint has a landmark and at least one candidate
    shares it, drop candidates that don't. If no candidate matches the
    landmark, fall through (the section itself was likely renamed —
    don't strand the heal on that)."""
    target = (stored.attributes.get("nearest_landmark_text") or "").strip()
    if not target:
        return candidates
    in_scope = [c for c in candidates if (c.attributes.get("nearest_landmark_text") or "").strip() == target]
    return in_scope if in_scope else candidates


# --- Locator derivation (mirrors inject.js's pickPrimaryLocator) ---------
def _derive_primary_locator(fp: ElementFingerprint) -> dict:
    """Pick the best locator from the candidate's attributes using the
    recorder's priority: id → name → css_path → xpath. (data-testid would
    fit between id and name but inject.js doesn't store it on attributes,
    only on the locator dicts — so if the candidate already has a
    data-testid primary, prefer that.)"""
    attrs = fp.attributes
    # Honour the candidate's own primary if it's a data-testid hit — that
    # signal isn't in `attributes` so we can only see it via the locator.
    pri = fp.primary_locator or {}
    if pri.get("strategy") == "data-testid" and pri.get("value"):
        return dict(pri)
    if attrs.get("id"):
        return {"strategy": "id", "value": attrs["id"]}
    if attrs.get("name"):
        return {"strategy": "name", "value": attrs["name"]}
    if attrs.get("css_path"):
        return {"strategy": "css", "value": attrs["css_path"]}
    if attrs.get("xpath"):
        return {"strategy": "xpath", "value": attrs["xpath"]}
    return dict(pri) if pri else {"strategy": "css", "value": ""}


def _derive_fallback_locators(fp: ElementFingerprint, primary: dict) -> list[dict]:
    attrs = fp.attributes
    out: list[dict] = []
    if attrs.get("id"):
        out.append({"strategy": "id", "value": attrs["id"]})
    # data-testid only available via existing locator list
    for loc in fp.fallback_locators or []:
        if loc.get("strategy") == "data-testid" and loc.get("value"):
            out.append(dict(loc))
    if attrs.get("name"):
        out.append({"strategy": "name", "value": attrs["name"]})
    if attrs.get("css_path"):
        out.append({"strategy": "css", "value": attrs["css_path"]})
    if attrs.get("xpath"):
        out.append({"strategy": "xpath", "value": attrs["xpath"]})
    # Dedupe and drop the primary
    seen = {(primary.get("strategy"), primary.get("value"))}
    deduped: list[dict] = []
    for loc in out:
        key = (loc.get("strategy"), loc.get("value"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(loc)
    return deduped


# --- Decision -------------------------------------------------------------
def select_match(
    stored: ElementFingerprint,
    candidates: list[ElementFingerprint],
    *,
    action: str,
    ai_matcher=None,
    score_threshold: float = SCORE_THRESHOLD,
    gray_low: float = GRAY_LOW,
    margin_req: float = MARGIN_REQ,
) -> HealDecision:
    """Pick the best heal for `stored` from `candidates`, or report unresolved.

    Filters applied in order:
      1. Action-compat (drop candidates the stored action can't run on)
      2. Landmark scope (drop out-of-section candidates if any match)
      3. Score
      4. Threshold + margin check; gray-zone AI confirmation
    """
    # Filter 1: action compatibility
    compat = [c for c in candidates if is_action_compatible(action, c)]
    if not compat:
        return HealDecision.unresolved(
            f"no candidate compatible with action {action!r} "
            f"(scanned {len(candidates)} interactive element(s))"
        )

    # Filter 2: landmark scope
    scoped = _filter_by_landmark(stored, compat)

    # Score all remaining candidates
    scored: list[tuple[float, list[str], ElementFingerprint]] = []
    for c in scoped:
        s, matched = score_candidate(stored, c)
        scored.append((s, matched, c))
    scored.sort(key=lambda t: t[0], reverse=True)

    top_score, top_matched, top_fp = scored[0]
    runner_up_score = scored[1][0] if len(scored) > 1 else 0.0
    margin = top_score - runner_up_score

    top_k: list[CandidateRef] = []
    for sc, _matched, fp in scored[:3]:
        primary = _derive_primary_locator(fp)
        fallbacks = _derive_fallback_locators(fp, primary)
        top_k.append(CandidateRef(
            primary_locator=primary,
            fallback_locators=fallbacks,
            attributes=dict(fp.attributes),
            score=sc,
        ))

    def _top_n_diag(n: int = 3) -> str:
        parts = []
        for i, (sc, _, fp) in enumerate(scored[:n]):
            lbl = fp.attributes.get("nearest_label_text") or fp.attributes.get("name") or fp.attributes.get("id") or "?"
            parts.append(f"#{i+1} {lbl!r} score={sc:.2f}")
        return "; ".join(parts)

    # Auto-heal
    if top_score >= score_threshold and margin >= margin_req:
        primary = _derive_primary_locator(top_fp)
        fallbacks = _derive_fallback_locators(top_fp, primary)
        return HealDecision(
            method="auto",
            matched_candidate=top_fp,
            confidence=top_score,
            runner_up_score=runner_up_score,
            matched_by=top_matched,
            new_primary_locator=primary,
            new_fallback_locators=fallbacks,
            diagnostics=_top_n_diag(),
            top_k_candidates=top_k,
        )

    # Gray zone: AI confirmation
    if top_score >= gray_low and ai_matcher is not None and getattr(ai_matcher, "is_available", lambda: False)():
        try:
            ai_result = ai_matcher.match_element(
                _fingerprint_to_legacy_dict(stored),
                [_fingerprint_to_legacy_dict(top_fp)],
            )
        except Exception:
            ai_result = None
        if ai_result and ai_result.get("match_index") == 0 and ai_result.get("confidence", 0) >= 0.7:
            primary = _derive_primary_locator(top_fp)
            fallbacks = _derive_fallback_locators(top_fp, primary)
            return HealDecision(
                method="ai-confirmed",
                matched_candidate=top_fp,
                confidence=top_score,
                runner_up_score=runner_up_score,
                matched_by=top_matched,
                new_primary_locator=primary,
                new_fallback_locators=fallbacks,
                diagnostics=f"AI confirmed top candidate (conf={ai_result.get('confidence', 0):.2f}); {_top_n_diag()}",
                top_k_candidates=top_k,
            )

    # Unresolved — explain why
    if top_score < gray_low:
        why = f"best candidate scored {top_score:.2f}, below floor {gray_low:.2f}"
    elif margin < margin_req:
        why = f"top score {top_score:.2f} but margin over runner-up only {margin:.2f} (need {margin_req:.2f}) — ambiguous"
    else:
        why = f"top score {top_score:.2f} in gray zone, no AI confirmation available"
    return HealDecision.unresolved(diagnostics=f"{why}; {_top_n_diag()}", runner_up_score=runner_up_score, top_k_candidates=top_k)


def _fingerprint_to_legacy_dict(fp: ElementFingerprint) -> dict:
    """Adapter: present an ElementFingerprint to the existing AIMatcher,
    which was built for the Excel-row element schema."""
    a = fp.attributes
    return {
        "element_name": a.get("nearest_label_text") or a.get("name") or a.get("id") or "",
        "element_type": a.get("tag", "") + (f":{a['type']}" if a.get("type") else ""),
        "locator_id": a.get("id", ""),
        "locator_name": a.get("name", ""),
        "locator_css": a.get("css_path", ""),
        "locator_xpath": a.get("xpath", ""),
        "locator_label": a.get("nearest_label_text", ""),
        "placeholder": a.get("placeholder", ""),
    }


# --- Browser-driven entrypoint -------------------------------------------
async def attempt_heal(
    page: Page,
    stored: ElementFingerprint,
    *,
    action: str,
    ai_matcher=None,
    score_threshold: float = SCORE_THRESHOLD,
    gray_low: float = GRAY_LOW,
    margin_req: float = MARGIN_REQ,
    force_candidate_index: int | None = None,
) -> HealDecision:
    """Scan the live page and attempt a heal for `stored`.

    Assumes inject.js has been added as an init script on the context.
    If `window.__sha.scanAll` is missing (e.g. a stale tab without
    injection), the heal returns unresolved with a diagnostic.
    """
    stored_url = (stored.page_context or {}).get("url", "")
    if not urls_compatible(stored_url, page.url):
        return HealDecision.unresolved(
            diagnostics=f"URL context mismatch: recorded on {stored_url!r}, replaying on {page.url!r}"
        )

    try:
        raw = await page.evaluate(
            "() => (window.__sha && window.__sha.scanAll) ? window.__sha.scanAll() : null"
        )
    except Exception as e:
        return HealDecision.unresolved(diagnostics=f"live-page scan failed: {type(e).__name__}: {e}")

    if raw is None:
        return HealDecision.unresolved(
            diagnostics="injected scanner missing on page (window.__sha.scanAll not present)"
        )

    candidates = []
    for d in raw:
        try:
            candidates.append(ElementFingerprint.from_dict(d))
        except Exception:
            continue

    if not candidates:
        return HealDecision.unresolved(diagnostics="no interactive elements found on page")

    decision = select_match(
        stored,
        candidates,
        action=action,
        ai_matcher=ai_matcher,
        score_threshold=score_threshold,
        gray_low=gray_low,
        margin_req=margin_req,
    )

    # Caller has explicitly demanded a specific candidate (used by the
    # runner-up retry path). Skip threshold/margin gating: the user has
    # already opted into "try this one even if it scored lower."
    if (
        force_candidate_index is not None
        and 0 <= force_candidate_index < len(decision.top_k_candidates)
    ):
        chosen = decision.top_k_candidates[force_candidate_index]
        return HealDecision(
            method="forced",
            confidence=chosen.score,
            runner_up_score=(
                decision.top_k_candidates[1].score
                if len(decision.top_k_candidates) > 1 else 0.0
            ),
            matched_by=[],   # not meaningful for a forced choice
            new_primary_locator=dict(chosen.primary_locator),
            new_fallback_locators=[dict(x) for x in chosen.fallback_locators],
            top_k_candidates=list(decision.top_k_candidates),
            diagnostics=f"forced candidate index {force_candidate_index}",
        )

    return decision
