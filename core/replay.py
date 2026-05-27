from __future__ import annotations
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit
from playwright.async_api import async_playwright, Page, Locator


# Flutter web emits <flt-semantics id="flt-semantic-node-N"> wrappers where
# N is a sequential ordinal assigned at render time. The same N points at
# completely different elements between sessions — sometimes between
# renders within a session. A stored locator referencing this pattern is
# strictly worse than no locator: it produces a "match" by coincidence
# on whatever node was Nth this run, and the healer (which can use
# text_content / bbox to find the real element) never gets called.
# Strip these from the locator chain so the heal path takes over.
_FLUTTER_ORDINAL_RE = re.compile(r"flt-semantic-node-\d+")


def _is_flutter_ordinal_locator(loc_dict: dict) -> bool:
    """Return True if this locator references a Flutter sequential ordinal.

    Matches all three flavours the recorder produces from the same source:
    `id`/`flt-semantic-node-12`, `css`/`flt-semantics#flt-semantic-node-12`,
    `xpath`/`//*[@id='flt-semantic-node-12']`.
    """
    value = loc_dict.get("value", "") or ""
    return bool(_FLUTTER_ORDINAL_RE.search(value))

from core.capture import load_inject_js
from core.recording import ElementFingerprint, Step, Recording
from core.replay_healer import HealDecision, attempt_heal


class ElementNotFound(RuntimeError):
    """Raised when no locator (primary or fallback) matches a fingerprint."""


@dataclass
class HealContext:
    """Carries per-run heal state through find_element_by_fingerprint.

    `cache` maps stored `ElementFingerprint.id` to the HealDecision we
    accepted on first miss, so subsequent steps touching the same element
    skip the scan + score pass.

    `last_decision` is set on every heal attempt (success or unresolved)
    so the caller can attach diagnostics to the step result.

    `action` is set per-step before invoking find_element_by_fingerprint
    — the healer needs it to enforce action-compatibility.

    `force_runner_up` maps fingerprint id to a top_k_candidates index —
    used by the runner-up retry path in the run report.

    `pre_submit_snapshot` maps step index -> list of required-field
    fingerprints captured just before a submit-like action runs. A
    downstream failure can diff against this state to identify newly
    required fields that the recording never knew to fill.

    `pre_submit_schema_snapshot` maps step index -> list of ALL-field
    fingerprints captured just before a submit-like action runs (via
    scanAll, not scanRequiredFields). Used by the post-run schema diff
    against Recording.record_time_fields to surface fields added to the
    form after the recording was made — including optional ones, which
    `pre_submit_snapshot` misses.
    """
    action: str = ""
    ai_matcher: object | None = None
    cache: dict[str, HealDecision] = field(default_factory=dict)
    last_decision: Optional[HealDecision] = None
    force_runner_up: dict[str, int] = field(default_factory=dict)
    pre_submit_snapshot: dict[int, list[dict]] = field(default_factory=dict)
    pre_submit_schema_snapshot: dict[int, list[dict]] = field(default_factory=dict)


def _locator_for(page: Page, locator: dict) -> Locator:
    strategy = locator["strategy"]
    value = locator["value"]
    if strategy == "id":
        return page.locator(f"#{value}")
    if strategy == "data-testid":
        return page.locator(f"[data-testid='{value}']")
    if strategy == "name":
        return page.locator(f"[name='{value}']")
    if strategy == "css":
        return page.locator(value)
    if strategy == "xpath":
        return page.locator(f"xpath={value}")
    raise ValueError(f"unknown locator strategy: {strategy!r}")


def _is_step_skippable(step: Step, next_step: Step | None = None) -> bool:
    """Decide whether a missing-element failure on this step is safe to skip.

    Rule:
      - submit/navigate/wait/press/hover -> never skippable (flow-advancing)
      - click on a button/link/etc. -> blocker (advances the flow)
      - click on a form input/textarea/select that is immediately followed
        by a fill/select/check/uncheck on the SAME fingerprint id ->
        skippable. The recorder captures a focus-click (tab/mouse into
        field) before typing; if the element is gone, dropping the click
        is safe — the next step carries the real intent and is itself
        evaluated for skippability.
      - click on a form input with no same-element follow-up -> blocker
      - select/check/uncheck on a required field -> blocker
      - select/check/uncheck on an optional field -> skippable
      - fill on an optional field -> skippable
      - fill on a required field with an empty/None recorded value -> skippable
        (recorder had nothing to fill anyway — the field's been deleted on a
        page that didn't actually need it)
      - fill on a required field with a real value -> blocker

    Steps with no element (navigate, wait) never reach the heal path —
    return False defensively so the caller can't accidentally skip them.
    """
    if step.element is None:
        return False
    if step.action in ("submit", "navigate", "wait", "press", "hover"):
        return False

    if step.action == "click":
        if (
            next_step is not None
            and next_step.element is not None
            and next_step.element.id == step.element.id
            and next_step.action in ("fill", "select", "check", "uncheck")
        ):
            tag = (step.element.attributes.get("tag") or "").lower()
            if tag in ("input", "textarea", "select"):
                return True
        return False

    constraints = step.element.attributes.get("html5_constraints") or {}
    is_required = bool(constraints.get("required"))

    if step.action == "fill":
        if not is_required:
            return True
        # Required field but recorder didn't fill it — safe to skip
        return not (step.value or "").strip()

    if step.action in ("select", "check", "uncheck"):
        return not is_required

    return False


async def find_element_by_fingerprint(
    page: Page,
    fp: ElementFingerprint,
    *,
    timeout_ms: int = 5000,
    poll_ms: int = 200,
    heal_context: Optional[HealContext] = None,
) -> Locator:
    """Try the primary locator, then each fallback. Return the first match.

    Match means count() >= 1 — we accept the first locator that resolves to at
    least one element. Callers use .first at action time.

    The full candidate list is retried every `poll_ms` until `timeout_ms`
    elapses so dynamically rendered forms (schema fetched after page load)
    don't fail step 0 instantly.

    If `heal_context` is provided and every stored locator misses, the
    healer is consulted before raising — see core.replay_healer. A
    successful heal returns a Locator from the new (live-discovered)
    primary locator and writes the HealDecision into the context.
    """
    candidates = [fp.primary_locator, *fp.fallback_locators]
    # Strip Flutter ordinal locators (see `_is_flutter_ordinal_locator`).
    # If every locator references the ordinal pattern, `candidates` becomes
    # empty and the loop falls straight through to the healer, which uses
    # text_content / bbox / role to find the right element by appearance
    # rather than by misleading id.
    candidates = [c for c in candidates if not _is_flutter_ordinal_locator(c)]
    # No usable stored locator → skip the polling wait and let the healer
    # take over immediately. Without this, the loop would burn the full
    # `timeout_ms` polling an empty candidate list before reaching the
    # heal path, which is just wasted time on Flutter-only recordings.
    deadline = time.monotonic() + (max(timeout_ms, 0) / 1000.0 if candidates else 0)
    last_err: Exception | None = None

    # Heal cache short-circuit: a prior step healed this same fingerprint id,
    # try the new primary first.
    if heal_context is not None:
        cached = heal_context.cache.get(fp.id)
        if cached and cached.new_primary_locator:
            try:
                loc = _locator_for(page, cached.new_primary_locator)
                if await loc.count() >= 1:
                    heal_context.last_decision = cached
                    return loc
            except Exception:
                pass  # cached locator no longer resolves; fall through

    while True:
        for loc_dict in candidates:
            try:
                loc = _locator_for(page, loc_dict)
                if await loc.count() >= 1:
                    return loc
            except Exception as e:
                last_err = e
                continue
        if time.monotonic() >= deadline:
            # Last resort: ask the healer. Only when caller opted in by
            # passing a context — keeps the no-heal callers (tests, simple
            # uses) on the previous behaviour.
            if heal_context is not None:
                decision = await attempt_heal(
                    page, fp,
                    action=heal_context.action,
                    ai_matcher=heal_context.ai_matcher,
                    force_candidate_index=heal_context.force_runner_up.get(fp.id),
                )
                heal_context.last_decision = decision
                if decision.method != "unresolved" and decision.new_primary_locator:
                    heal_context.cache[fp.id] = decision
                    try:
                        loc = _locator_for(page, decision.new_primary_locator)
                        if await loc.count() >= 1:
                            return loc
                    except Exception as e:
                        last_err = e
                # Fall through to raise. The decision is on the context for
                # the caller to render in diagnostics.
                diag = f"; healer: {decision.diagnostics}" if decision.diagnostics else ""
            else:
                diag = ""
            raise ElementNotFound(
                f"no locator matched for fingerprint {fp.id}; tried {len(candidates)} strategies"
                + (f" within {timeout_ms}ms" if timeout_ms > 0 else "")
                + (f"; last error: {last_err}" if last_err else "")
                + diag
            )
        await page.wait_for_timeout(poll_ms)


async def _await_manual_resume(page: Page, step: "Step") -> None:
    """Pause the replay and surface an on-page banner with a Resume button.

    Used for steps marked `needs_manual=True` (captcha, OTP, security
    questions). The replay is meant to be running in headed mode here, so the
    user can see the page, perform whatever interaction the step needs (e.g.
    type the live captcha into the field themselves), and then click the
    banner's Resume button. The replay then skips the step's automatic
    action — the human has already done whatever was needed — and proceeds
    to the next step.

    The banner is injected via `page.evaluate`, lives in a fixed-position
    overlay so the rest of the page stays interactive, and removes itself on
    Resume. We wait on a JS sentinel (`window.__sha_manual_resumed`) using
    `wait_for_function` with no timeout — the user takes as long as they
    take. No subprocess/Streamlit IPC is needed: everything happens inside
    the Playwright browser the user is already looking at.
    """
    field_label = ""
    if step.element is not None:
        attrs = step.element.attributes or {}
        field_label = (
            attrs.get("nearest_label_text")
            or attrs.get("aria_label")
            or attrs.get("placeholder")
            or attrs.get("name")
            or step.element.primary_locator.get("value", "")
            or ""
        )
    safe_label = (field_label or step.action).replace("`", "'").replace("\\", "\\\\")
    safe_action = step.action.replace("`", "'")
    await page.evaluate(
        """([action, label]) => {
            // Drop any prior banner from an earlier paused step.
            const prev = document.getElementById('__sha_manual_banner');
            if (prev) prev.remove();
            window.__sha_manual_resumed = false;

            const div = document.createElement('div');
            div.id = '__sha_manual_banner';
            div.style.cssText = [
                'position:fixed', 'top:16px', 'right:16px', 'z-index:2147483647',
                'background:#fff8d1', 'border:2px solid #b8860b', 'border-radius:10px',
                'padding:14px 18px', 'font-family:system-ui,sans-serif', 'font-size:14px',
                'color:#222', 'box-shadow:0 8px 24px rgba(0,0,0,0.25)', 'max-width:360px',
            ].join(';');
            div.innerHTML =
                '<div style="font-weight:700;font-size:15px;margin-bottom:6px">' +
                '⏸ Replay paused — manual step</div>' +
                '<div style="margin-bottom:10px;line-height:1.4">' +
                'This step (<code>' + action + '</code> on <b>' + label + '</b>) ' +
                'can\\'t be replayed automatically (captcha, OTP, etc.). ' +
                'Complete it in the page yourself, then click Resume.</div>' +
                '<button id="__sha_resume_btn" style="background:#0a5aa5;color:#fff;' +
                'border:none;border-radius:6px;padding:8px 16px;font-size:14px;' +
                'cursor:pointer;font-weight:600">Resume automation ▶</button>';
            document.body.appendChild(div);
            document.getElementById('__sha_resume_btn').addEventListener('click', () => {
                window.__sha_manual_resumed = true;
                div.remove();
            });
        }""",
        [safe_action, safe_label],
    )
    # No timeout — the human takes as long as they need.
    await page.wait_for_function(
        "() => window.__sha_manual_resumed === true", timeout=0
    )


async def _pick_user_visible_target(loc):
    """Choose the locator match that the user actually interacts with.

    Some pages render the same form/component twice in the DOM at identical
    coordinates (React StrictMode double-mount, SSR/CSR hydration leftovers,
    routes mounting the same child twice). Both copies pass every standard
    visibility check — `is_visible`, `:visible`, bbox, computed style — yet
    only one of them is on top at its center; the other is occluded. Playwright's
    `loc.first` picks the first match in DOM order, which can be the covered
    copy, causing fills/clicks to "succeed" against an invisible element.

    `document.elementFromPoint(centerX, centerY)` is the only reliable
    disambiguator. When `count == 1` (the overwhelmingly common case) we skip
    the check and return `loc.first` directly — one extra count() roundtrip,
    no per-handle work. When no candidate is topmost (off-screen, covered by
    an overlay, inside an iframe), we fall back to `loc.first` so behavior
    is never worse than before.
    """
    n = await loc.count()
    if n <= 1:
        return loc.first
    for i in range(n):
        try:
            is_top = await loc.nth(i).evaluate(
                """el => {
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) return false;
                    const cx = r.left + r.width / 2;
                    const cy = r.top + r.height / 2;
                    return document.elementFromPoint(cx, cy) === el;
                }"""
            )
        except Exception:
            continue
        if is_top:
            return loc.nth(i)
    return loc.first


async def execute_step(
    page: Page,
    step: Step,
    override: str | None,
    *,
    element_timeout_ms: int = 5000,
    heal_context: Optional[HealContext] = None,
) -> None:
    """Run one recorded step against `page`.

    `override` lets callers (test-case replay) substitute a different value
    for the same step without mutating the Recording. If None, the step's
    recorded value is used.

    If `heal_context` is provided, the healer is consulted on locator
    miss. The context's `action` is set from this step's action so the
    healer can enforce action-compatibility on candidates.
    """
    value = override if override is not None else step.value
    if step.action == "navigate":
        await page.goto(value or "")
        return
    if step.action == "wait":
        await page.wait_for_timeout(int(value or 0))
        return
    if step.element is None:
        raise ValueError(f"step {step.index} action={step.action!r} requires an element fingerprint")
    if heal_context is not None:
        heal_context.action = step.action
        heal_context.last_decision = None
    loc = await find_element_by_fingerprint(
        page, step.element,
        timeout_ms=element_timeout_ms,
        heal_context=heal_context,
    )
    # Pre-submit scan: for click/submit actions that target a button,
    # snapshot the form's required fields so a downstream failure can
    # diff against this state.
    is_submit_like = (
        step.action in ("click", "submit")
        and step.element is not None
        and (
            step.element.attributes.get("tag", "").lower() in ("button", "input")
            or step.element.attributes.get("role", "").lower() == "button"
        )
    )
    if is_submit_like and heal_context is not None:
        try:
            required_list = await page.evaluate("window.__sha.scanRequiredFields()")
            heal_context.pre_submit_snapshot[step.index] = required_list
        except Exception:
            pass  # Non-fatal — scan failure shouldn't abort the step.
        # Full-form schema snapshot for the post-run schema diff against
        # Recording.record_time_fields. Distinct from pre_submit_snapshot
        # (which is required-only); this one catches optional new fields.
        try:
            schema_list = await page.evaluate("window.__sha.scanAll()")
            heal_context.pre_submit_schema_snapshot[step.index] = schema_list
        except Exception:
            pass

    target = await _pick_user_visible_target(loc)
    if step.needs_manual:
        # Human performs this step manually. We resolved the locator so the
        # healer's pre-action scans (URL match, action-compat guard) still
        # run as guardrails, but the user types the captcha/OTP themselves.
        await _await_manual_resume(page, step)
        return
    if step.action == "fill":
        await target.fill(value or "")
    elif step.action == "click" or step.action == "submit":
        # Flyout submenu items frequently have zero-height/zero-width <a>
        # wrappers — the visible click area lives on a sibling/parent, but the
        # <a> is what carries the href and what the user "clicked" at record
        # time. force=True skips actionability checks but Playwright still
        # tries scrollIntoView, which can't anchor on a zero-size box and then
        # fails with "Element is outside of the viewport". Dispatch the click
        # in JS instead — for <a href> this triggers native navigation just
        # like a user click. See dogfood-output/hover_proto_results.md.
        bbox = (step.element.attributes.get("bbox") or {}) if step.element else {}
        zero_bbox = (
            isinstance(bbox, dict)
            and (float(bbox.get("width") or 0) == 0 or float(bbox.get("height") or 0) == 0)
        )
        if zero_bbox:
            await target.evaluate("el => el.click()")
        else:
            await target.click()
    elif step.action == "hover":
        # Hover steps materialize flyouts, tooltips, etc. that subsequent
        # clicks depend on. Captured by the recorder's hover-chain detector
        # (see core/capture/inject.js `_detectHoverChain`).
        await target.hover()
        # Many menu frameworks debounce open (Ant Design's mouseEnterDelay
        # defaults to ~100ms) and then play a short open animation. Without
        # this settle wait, the next step's locator probes the DOM before the
        # flyout is attached and fails with ElementNotFound. 500ms covers the
        # common cases (Ant, MUI, Bootstrap, plain CSS transitions) without
        # being long enough to feel sluggish.
        await page.wait_for_timeout(500)
    elif step.action == "select":
        await target.select_option(value or "")
    elif step.action == "check":
        await target.check()
    elif step.action == "uncheck":
        await target.uncheck()
    elif step.action == "press":
        await target.press(value or "")
    else:
        raise ValueError(f"unsupported action: {step.action!r}")


@dataclass
class ReplayOutcome:
    completed_steps: int = 0
    healed_steps: int = 0
    failed_step_index: Optional[int] = None
    error: Optional[str] = None
    final_url: str = ""
    step_results: list[dict] = field(default_factory=list)
    run_dir: Optional[str] = None
    promoted_heals: list[dict] = field(default_factory=list)  # one entry per heal written back
    run_id: str = ""
    new_required_fields_detected: list[dict] = field(default_factory=list)
    auto_filled_fields: list[dict] = field(default_factory=list)
    original_failure: Optional[dict] = None
    # One entry per step that was skipped because the healer classified
    # the element as field_removed AND the step was safe to skip
    # (see _is_step_skippable). Each entry: {step_index, action, fingerprint_id,
    # field_label, diagnostics}. Does NOT include the post-failure cascade
    # (those still appear in step_results with status="skipped").
    skipped_steps: list[dict] = field(default_factory=list)
    # Each entry: {step_index, expected_url, actual_url}. Populated before a
    # step runs when the live page URL doesn't match the URL captured at
    # record time. Warning-only in this iteration — the step still attempts
    # to execute; the entry exists so the run report can flag "you replayed
    # on the wrong page" cases that today surface as confusing "ambiguous
    # fingerprint" failures.
    page_context_warnings: list[dict] = field(default_factory=list)


def _normalize_url_for_compare(url: str) -> str:
    """Reduce a URL to the parts that should match across record and replay.

    Drops the query string (tracking params, session tokens) but keeps the
    fragment — SPAs (notably Flutter web) put their route in the hash, so
    `/webapp/#/internal/dashboard` vs `/webapp/#/internal/newApplication`
    is the signal we care about, not noise.
    """
    if not url:
        return ""
    p = urlsplit(url)
    base = f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")
    return base + (f"#{p.fragment}" if p.fragment else "")


def _promote_heals_to_recording(
    recording_path: str,
    *,
    promoted: dict[str, "HealDecision"],
    run_id: str,
) -> list[dict]:
    """Apply heals to a recording on disk. For each fingerprint id in
    `promoted`, push the current fingerprint into history and replace it
    with the healed primary locator + candidate attributes.

    Returns a list of summary dicts (one per applied heal) for the run
    report. Pruning: history is capped at 10 entries per fingerprint.
    """
    from datetime import datetime, timezone
    from core.recording import HistoryEntry, load_recording, save_recording

    rec = load_recording(recording_path)
    now = datetime.now(timezone.utc).isoformat()
    summaries: list[dict] = []
    HISTORY_CAP = 10

    for step in rec.steps:
        if step.element is None:
            continue
        decision = promoted.get(step.element.id)
        if decision is None:
            continue
        fp = step.element
        # Push current state into history
        entry = HistoryEntry(
            timestamp=now,
            run_id=run_id,
            source="heal",
            confidence=float(decision.confidence),
            previous_primary_locator=dict(fp.primary_locator),
            previous_fallback_locators=[dict(x) for x in fp.fallback_locators],
            previous_attributes=dict(fp.attributes),
        )
        fp.fingerprint_history.append(entry)
        if len(fp.fingerprint_history) > HISTORY_CAP:
            fp.fingerprint_history = fp.fingerprint_history[-HISTORY_CAP:]
        # Replace active locator + attributes
        old_primary = dict(fp.primary_locator)
        fp.primary_locator = dict(decision.new_primary_locator or fp.primary_locator)
        fp.fallback_locators = [dict(x) for x in (decision.new_fallback_locators or [])]
        if decision.top_k_candidates:
            fp.attributes = dict(decision.top_k_candidates[0].attributes)
        summaries.append({
            "fingerprint_id": fp.id,
            "step_index": step.index,
            "old_primary_locator": old_primary,
            "new_primary_locator": dict(fp.primary_locator),
            "confidence": float(decision.confidence),
            "method": decision.method,
        })

    rec.healed_at = now
    save_recording(recording_path, rec)
    return summaries


def _revert_last_heal_in_recording(
    recording_path: str,
    *,
    fingerprint_id: str,
) -> bool:
    """Pop the most recent history entry for the matching fingerprint and
    restore its previous state. The current state is pushed into history
    first so revert is itself revertable. Returns True on success."""
    from datetime import datetime, timezone
    from core.recording import HistoryEntry, load_recording, save_recording

    rec = load_recording(recording_path)
    for step in rec.steps:
        fp = step.element
        if fp is None or fp.id != fingerprint_id:
            continue
        if not fp.fingerprint_history:
            return False
        prev = fp.fingerprint_history.pop()
        # Push current state into history so revert is revertable
        now = datetime.now(timezone.utc).isoformat()
        fp.fingerprint_history.append(HistoryEntry(
            timestamp=now,
            run_id="<revert>",
            source="heal",
            confidence=prev.confidence,
            previous_primary_locator=dict(fp.primary_locator),
            previous_fallback_locators=[dict(x) for x in fp.fallback_locators],
            previous_attributes=dict(fp.attributes),
        ))
        # Restore previous state
        fp.primary_locator = dict(prev.previous_primary_locator)
        fp.fallback_locators = [dict(x) for x in prev.previous_fallback_locators]
        fp.attributes = dict(prev.previous_attributes)
        save_recording(recording_path, rec)
        return True
    return False


def _revert_last_heal(*, scenario, recording_id: str, fingerprint_id: str) -> bool:
    """Apply _revert_last_heal_in_recording against a sidecar then merge
    the result into the scenario's recordings list. Returns True on success."""
    import os
    from core.recording import Recording, save_recording, load_recording
    from core.scenarios import save_scenario

    rec_dict = next(
        (r for r in scenario.recordings if r.get("id") == recording_id), None,
    )
    if rec_dict is None:
        return False
    work = os.path.join("data/replay_runs", recording_id)
    os.makedirs(work, exist_ok=True)
    side = os.path.join(work, "_revert_recording.yaml")
    save_recording(side, Recording.from_dict(rec_dict))
    ok = _revert_last_heal_in_recording(side, fingerprint_id=fingerprint_id)
    if not ok:
        return False
    reloaded = load_recording(side)
    for i, r in enumerate(scenario.recordings):
        if r.get("id") == recording_id:
            scenario.recordings[i] = reloaded.to_dict()
            break
    save_scenario("data/scenarios", scenario)
    return True


def _save_auto_filled_steps(
    *,
    scenario,
    recording_id: str,
    auto_filled: list[dict],
    insert_before_step_index: int = 0,
) -> None:
    """Insert AI-suggested fill steps into the recording on the scenario,
    persist the scenario. Each inserted step is marked inserted_by='auto-heal'.

    Each `auto_filled` entry may carry its own `insert_before_step_index`
    (set by the wrapper). The top-level `insert_before_step_index` arg is
    a fallback for entries that don't carry their own — kept for
    backwards-compat with callers built around the reactive path's single
    submit-step assumption."""
    from core.recording import Recording, Step, ElementFingerprint
    from core.scenarios import save_scenario

    rec_dict = next(
        (r for r in scenario.recordings if r.get("id") == recording_id), None,
    )
    if rec_dict is None:
        return
    rec = Recording.from_dict(rec_dict)
    # Sort entries by their target insertion index so insertions on later
    # indices don't shift earlier ones around unexpectedly.
    sorted_fills = sorted(
        auto_filled,
        key=lambda af: af.get("insert_before_step_index", insert_before_step_index),
    )
    bumped = 0  # how many steps we've already inserted before this point
    last_idx = -1
    for af in sorted_fills:
        target = af.get("insert_before_step_index", insert_before_step_index)
        if target == last_idx:
            # Multiple fills targeting the same submit step → keep them
            # contiguous, in the order they came in.
            insert_at = max(0, target) + bumped
        else:
            insert_at = max(0, target) + bumped
            last_idx = target
        new_step = Step(
            index=0,  # rewritten below
            action="fill",
            value=af["value"],
            element=ElementFingerprint(
                id=af["fingerprint_id"],
                primary_locator=af.get("primary_locator") or {},
                fallback_locators=af.get("fallback_locators") or [],
                attributes=af.get("attributes") or {},
                page_context={},
            ),
            inserted_by="auto-heal",
        )
        rec.steps.insert(insert_at, new_step)
        bumped += 1
    for i, s in enumerate(rec.steps):
        s.index = i
    for i, r in enumerate(scenario.recordings):
        if r.get("id") == recording_id:
            scenario.recordings[i] = rec.to_dict()
            break
    save_scenario("data/scenarios", scenario)


def _schema_diff_new_fields(
    *,
    record_time_fields: list[dict],
    pre_submit_schema_snapshot: dict[int, list[dict]],
    already_detected_keys: set[tuple[str, str]],
) -> list[dict]:
    """Diff the live-scan schema against the recording's record_time_fields.

    Anything present in the live scan whose (name, nearest_label_text) does
    NOT match any record-time entry (exact, then fuzzy >= 0.85 on label) is
    treated as "newly added since recording." Each returned entry carries
    is_required read from the LIVE DOM (not the recording), since required-
    ness can change independently of existence.

    `already_detected_keys` is the set of (name, label) tuples already
    surfaced by other detection paths (post-submit-error, pre-submit-diff).
    Entries matching one of those are skipped to avoid duplicates.
    """
    from difflib import SequenceMatcher

    record_names = {
        (rf.get("name") or "").strip().lower()
        for rf in record_time_fields
        if (rf.get("name") or "").strip()
    }
    record_labels = [
        (rf.get("nearest_label_text") or "").strip().lower()
        for rf in record_time_fields
        if (rf.get("nearest_label_text") or "").strip()
    ]

    def _has_match(name: str, label: str) -> bool:
        n = name.strip().lower()
        l = label.strip().lower()
        if n and n in record_names:
            return True
        if l:
            if l in record_labels:
                return True
            # Fuzzy on label — fields often share name but labels rephrase
            for rl in record_labels:
                if SequenceMatcher(None, l, rl).ratio() >= 0.85:
                    return True
        return False

    new_entries: list[dict] = []
    seen_keys: set[tuple[str, str]] = set(already_detected_keys)
    for submit_idx, current_fields in pre_submit_schema_snapshot.items():
        for fp in current_fields:
            attrs = fp.get("attributes") or {}
            name = (attrs.get("name") or "").strip()
            label = (attrs.get("nearest_label_text") or "").strip()
            if not name and not label:
                # No identity beyond locator — can't meaningfully diff.
                continue
            if _has_match(name, label):
                continue
            key = (name.lower(), label.lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            new_entries.append({
                "fingerprint": fp,
                "error_text": "",
                "discovery": "schema_diff",
                "submit_step_index": submit_idx,
                "is_required": bool(attrs.get("is_required", False)),
            })
    return new_entries


def _prune_replay_runs(recording_dir: str, keep: int = 5) -> None:
    """Keep only the most recent `keep` run subdirectories under recording_dir.

    Older directories are removed wholesale. Prevents EC2 disk from filling
    with per-step screenshots over time.
    """
    if not os.path.isdir(recording_dir):
        return
    entries = []
    for name in os.listdir(recording_dir):
        full = os.path.join(recording_dir, name)
        if os.path.isdir(full):
            entries.append((os.path.getmtime(full), full))
    entries.sort(reverse=True)
    for _, path in entries[keep:]:
        shutil.rmtree(path, ignore_errors=True)


async def replay_recording(
    recording: Recording,
    *,
    data_overrides: dict[str, str] | None = None,
    storage_state: dict | None = None,
    headless: bool = True,
    screenshot_dir: str | None = None,
    element_timeout_ms: int = 5000,
    ai_matcher: object | None = None,
    healing_enabled: bool = True,
    recording_path: str | None = None,
    promote_on_pass: bool = True,
    force_runner_up: dict[str, int] | None = None,
) -> ReplayOutcome:
    """Open a context, navigate to start_url, walk every step.

    `data_overrides` maps `ElementFingerprint.id` -> override value. Used by
    test cases; falls back to each step's recorded value when absent.

    If `screenshot_dir` is set, a fresh timestamped subdirectory is created
    under it and a screenshot is captured after every step (pass or fail).
    """
    overrides = data_overrides or {}
    outcome = ReplayOutcome()

    # A step marked needs_manual pauses replay and asks the user to act in
    # the page. That's impossible if the browser isn't visible, so override
    # headless when any step requires it. The caller's intent (headless=True
    # from CI etc.) is respected for recordings with no manual steps.
    if headless and any(getattr(s, "needs_manual", False) for s in recording.steps):
        headless = False

    run_dir: Optional[str] = None
    if screenshot_dir:
        ts = time.strftime("%Y%m%d_%H%M%S")
        run_dir = os.path.join(screenshot_dir, ts)
        os.makedirs(run_dir, exist_ok=True)
        outcome.run_dir = run_dir

    heal_context = (
        HealContext(
            ai_matcher=ai_matcher,
            force_runner_up=dict(force_runner_up or {}),
        )
        if healing_enabled else None
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx_kwargs = {}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        context = await browser.new_context(**ctx_kwargs)
        if healing_enabled:
            # The healer needs window.__sha.scanAll() available on every page
            # to enumerate live candidates. Inject before any navigation.
            await context.add_init_script(load_inject_js())
        page = await context.new_page()
        try:
            await page.goto(recording.start_url)
            failed = False
            for step in recording.steps:
                if failed:
                    outcome.step_results.append({
                        "step_index": step.index,
                        "action": step.action,
                        "value": step.value,
                        "status": "skipped",
                        "screenshot_path": None,
                        "error": None,
                    })
                    continue
                ovr = None
                if step.element is not None:
                    ovr = overrides.get(step.element.id)
                effective_value = ovr if ovr is not None else step.value
                result = {
                    "step_index": step.index,
                    "action": step.action,
                    "value": effective_value,
                    "status": "passed",
                    "screenshot_path": None,
                    "error": None,
                }
                # Page-context assertion (warning-only). Compare the live URL
                # against the URL recorded for this step's element. Mismatch
                # almost always means a navigation click was dropped at record
                # time — the step about to run targets a page we never reached.
                # Surfacing this BEFORE the step's fingerprint-miss noise turns
                # "ambiguous candidate" failures into "you're on the wrong page"
                # diagnostics. We don't fail the step here; downstream heal
                # logic still owns the verdict for this iteration.
                if step.element is not None:
                    expected_raw = step.element.page_context.get("url", "")
                    if expected_raw:
                        actual_raw = page.url
                        expected_norm = _normalize_url_for_compare(expected_raw)
                        actual_norm = _normalize_url_for_compare(actual_raw)
                        if expected_norm and expected_norm != actual_norm:
                            # SPA initial-redirect false positive: Flutter web
                            # (and similar) loads `/app` first, then internally
                            # navigates to `/app/#/route`. page.goto returns on
                            # the initial DOM, before the hash redirect has
                            # fired. Give the app up to 1.5s to settle when
                            # the expected URL has a fragment route but the
                            # actual URL doesn't yet. Outside that specific
                            # shape we don't wait — the URLs are genuinely
                            # different and waiting would just delay the
                            # warning for real mismatches.
                            exp_split = urlsplit(expected_raw)
                            act_split = urlsplit(actual_raw)
                            spa_redirect_pending = bool(
                                exp_split.fragment and not act_split.fragment
                            )
                            if spa_redirect_pending:
                                end_at = time.monotonic() + 1.5
                                while time.monotonic() < end_at:
                                    await page.wait_for_timeout(100)
                                    actual_raw = page.url
                                    actual_norm = _normalize_url_for_compare(actual_raw)
                                    if actual_norm == expected_norm:
                                        break
                            if expected_norm != actual_norm:
                                warning = {
                                    "step_index": step.index,
                                    "expected_url": expected_raw,
                                    "actual_url": actual_raw,
                                }
                                outcome.page_context_warnings.append(warning)
                                result["page_context_warning"] = warning
                try:
                    await execute_step(
                        page, step,
                        override=ovr,
                        element_timeout_ms=element_timeout_ms,
                        heal_context=heal_context,
                    )
                    outcome.completed_steps += 1
                    if heal_context is not None and heal_context.last_decision is not None:
                        d = heal_context.last_decision
                        if d.method != "unresolved":
                            result["healed"] = {
                                "method": d.method,
                                "confidence": d.confidence,
                                "runner_up_score": d.runner_up_score,
                                "matched_by": list(d.matched_by),
                                "old_primary_locator": dict(step.element.primary_locator) if step.element else None,
                                "new_primary_locator": dict(d.new_primary_locator or {}),
                                "new_fallback_locators": [dict(x) for x in (d.new_fallback_locators or [])],
                                "candidate_attrs": dict((d.matched_candidate.attributes if d.matched_candidate else {})),
                                "diagnostics": d.diagnostics,
                                "fingerprint_id": step.element.id if step.element else "",
                                "top_k_candidates": [
                                    {
                                        "primary_locator": dict(c.primary_locator),
                                        "fallback_locators": [dict(x) for x in c.fallback_locators],
                                        "attributes": dict(c.attributes),
                                        "score": float(c.score),
                                    }
                                    for c in (d.top_k_candidates or [])
                                ],
                            }
                            outcome.healed_steps += 1
                except Exception as e:
                    # Skip-and-continue policy: any heal verdict that means
                    # "we couldn't find this element" — `field_removed` OR
                    # `unresolved` — is safe to skip iff `_is_step_skippable`
                    # says yes (optional fills/toggles, never click/submit).
                    # The two verdicts mean different things and the report
                    # surfaces them with distinct copy:
                    #   - field_removed: scan found nothing close → field
                    #     looks deleted from the page
                    #   - unresolved:    scan found something close-ish but
                    #     not confident enough to commit a heal
                    # Blocker steps (click/submit/required-fill) still fail
                    # regardless of which verdict fired — the manual-fix CTA
                    # path remains the recovery route.
                    last = heal_context.last_decision if heal_context else None
                    last_method = last.method if last else None
                    skip_reason: Optional[str] = None
                    if last_method == "field_removed":
                        skip_reason = "field_removed"
                    elif last_method == "unresolved":
                        skip_reason = "unresolved"

                    next_step = (
                        recording.steps[step.index + 1]
                        if step.index + 1 < len(recording.steps)
                        else None
                    )
                    if skip_reason and _is_step_skippable(step, next_step):
                        status_value = (
                            "skipped_removed"
                            if skip_reason == "field_removed"
                            else "skipped_unresolved"
                        )
                        result["status"] = status_value
                        result["error"] = None
                        result["skip_reason"] = skip_reason
                        attrs = step.element.attributes if step.element else {}
                        field_label = (
                            attrs.get("nearest_label_text")
                            or attrs.get("aria_label")
                            or attrs.get("name")
                            or (step.element.primary_locator.get("value", "") if step.element else "")
                        )
                        skip_entry = {
                            "step_index": step.index,
                            "action": step.action,
                            "fingerprint_id": step.element.id if step.element else "",
                            "field_label": field_label,
                            "diagnostics": last.diagnostics,
                            "reason": skip_reason,
                        }
                        outcome.skipped_steps.append(skip_entry)
                        result["removal_diagnostics"] = last.diagnostics
                        # DO NOT set failed = True — next iteration continues.
                    else:
                        result["status"] = "failed"
                        result["error"] = f"{type(e).__name__}: {e}"
                        if heal_context is not None and heal_context.last_decision is not None:
                            d = heal_context.last_decision
                            result["heal_diagnostics"] = d.diagnostics
                            if d.method == "field_removed":
                                # Blocker step had a removed target — surface
                                # this distinctly so the UI can show
                                # "Add step manually" CTA.
                                result["removal_diagnostics"] = d.diagnostics
                                outcome.error = f"field_removed (blocker): {e}"
                            else:
                                outcome.error = f"{type(e).__name__}: {e}"
                        else:
                            outcome.error = f"{type(e).__name__}: {e}"
                        outcome.failed_step_index = step.index

                        # Existing post-submit error scan path (unchanged)
                        prev_step = recording.steps[step.index - 1] if step.index > 0 else None
                        prev_was_submit = (
                            prev_step is not None
                            and prev_step.action in ("click", "submit")
                        )
                        if prev_was_submit and heal_context is not None:
                            try:
                                errors = await page.evaluate("window.__sha.scanPostSubmitErrors()")
                                pre_required = heal_context.pre_submit_snapshot.get(prev_step.index, [])
                                filled_fp_ids = {
                                    s.element.id for s in recording.steps[:step.index]
                                    if s.element is not None and s.action in ("fill", "select", "check")
                                }
                                new_required = []
                                for req in pre_required:
                                    if not req.get("is_empty"):
                                        continue
                                    if req["id"] in filled_fp_ids:
                                        continue
                                    err_text = next(
                                        (err["error_text"] for err in errors
                                         if err.get("associated_field")
                                            and err["associated_field"]["id"] == req["id"]),
                                        "",
                                    )
                                    new_required.append({
                                        "fingerprint": req,
                                        "error_text": err_text,
                                        "discovery": "post_submit_failure",
                                        "submit_step_index": prev_step.index,
                                        # pre_submit_snapshot is scanRequiredFields
                                        # output — required by definition.
                                        "is_required": True,
                                    })
                                outcome.new_required_fields_detected = new_required
                            except Exception:
                                pass

                        failed = True
                if run_dir:
                    shot_path = os.path.join(run_dir, f"step_{step.index:03d}.png")
                    try:
                        await page.screenshot(path=shot_path, full_page=True)
                        result["screenshot_path"] = shot_path
                    except Exception:
                        result["screenshot_path"] = None
                outcome.step_results.append(result)
            outcome.final_url = page.url
        finally:
            await context.close()
            await browser.close()

    if screenshot_dir:
        _prune_replay_runs(screenshot_dir, keep=5)

    outcome.run_id = uuid.uuid4().hex[:12]

    # Proactive new-required-field detection (the "broadened trigger").
    # The reactive path above populates new_required_fields_detected only
    # when a submit click was followed by a failure. That misses forms
    # where the new field is required by the schema but the browser/server
    # didn't block submit (no `required` attribute on the input, server
    # silently accepts, etc.). After a passing run we do the same diff
    # — pre_submit_snapshot ∩ unfilled-by-recording — and surface any
    # remaining gap so the UI can offer "Add this step?" even on a green run.
    if (
        heal_context is not None
        and outcome.failed_step_index is None
        and not outcome.new_required_fields_detected
        and heal_context.pre_submit_snapshot
    ):
        filled_fp_ids = {
            s.element.id for s in recording.steps
            if s.element is not None and s.action in ("fill", "select", "check")
        }
        proactive: list[dict] = []
        seen_fp_ids: set[str] = set()
        for submit_idx, pre_required in heal_context.pre_submit_snapshot.items():
            for req in pre_required:
                if not req.get("is_empty"):
                    continue
                fp_id = req.get("id")
                if not fp_id or fp_id in filled_fp_ids or fp_id in seen_fp_ids:
                    continue
                seen_fp_ids.add(fp_id)
                proactive.append({
                    "fingerprint": req,
                    "error_text": "",
                    "discovery": "pre_submit_diff",
                    "submit_step_index": submit_idx,
                    # scanRequiredFields output — required by definition.
                    "is_required": True,
                })
        if proactive:
            outcome.new_required_fields_detected = proactive

    # Schema diff against Recording.record_time_fields (snapshot taken at
    # recording-save time). Catches fields added to the form since the
    # recording was made, including optional ones that scanRequiredFields
    # would miss. Fires regardless of failure state — both passing and
    # failing runs benefit from surfacing newly-added fields.
    #
    # Recordings made before this feature shipped have an empty
    # record_time_fields list; in that case we skip the diff so we don't
    # surface every field on the page as "new."
    if (
        heal_context is not None
        and recording.record_time_fields
        and heal_context.pre_submit_schema_snapshot
    ):
        already_keys: set[tuple[str, str]] = set()
        for nr in outcome.new_required_fields_detected:
            fp = nr.get("fingerprint") or {}
            attrs = fp.get("attributes") or {}
            already_keys.add((
                (attrs.get("name") or "").strip().lower(),
                (attrs.get("nearest_label_text") or "").strip().lower(),
            ))
        new_via_schema = _schema_diff_new_fields(
            record_time_fields=recording.record_time_fields,
            pre_submit_schema_snapshot=heal_context.pre_submit_schema_snapshot,
            already_detected_keys=already_keys,
        )
        if new_via_schema:
            outcome.new_required_fields_detected = (
                list(outcome.new_required_fields_detected) + new_via_schema
            )

    if (
        promote_on_pass
        and recording_path is not None
        and heal_context is not None
        and outcome.failed_step_index is None  # scenario passed end-to-end
        and heal_context.cache  # there's at least one heal to promote
    ):
        try:
            summaries = _promote_heals_to_recording(
                recording_path,
                promoted=dict(heal_context.cache),
                run_id=outcome.run_id,
            )
            outcome.promoted_heals = summaries
        except Exception as e:
            # Don't fail the run if promotion fails; surface in error
            outcome.error = f"heals not promoted: {e}"

    return outcome


async def replay_recording_with_auto_fill(
    recording: Recording,
    *,
    data_overrides: dict[str, str] | None = None,
    storage_state: dict | None = None,
    headless: bool = True,
    screenshot_dir: str | None = None,
    element_timeout_ms: int = 5000,
    ai_matcher: object | None = None,
    healing_enabled: bool = True,
    recording_path: str | None = None,
    promote_on_pass: bool = True,
    force_runner_up: dict[str, int] | None = None,
    auto_fill_overrides: dict[str, str] | None = None,
) -> ReplayOutcome:
    """Wrap replay_recording with an auto-retry path.

    Two surfaces:
      - **Reactive** (submit failed + new required field detected): rerun
        the scenario with an AI-filled fill-step inserted before the submit.
        Show success/failure banner with the value(s) used.
      - **Proactive** (submit passed but pre-submit scan vs. recording diff
        revealed an unfilled required field): no rerun — the run already
        passed without it. Surface the field with an AI-suggested value so
        the user can add it to the recording with one click.

    `auto_fill_overrides` is `{fingerprint_id: user_supplied_value}`. When
    present, the wrapper uses these values instead of `value_for_field` for
    the matching fingerprints. Lets the failure-banner "Edit & Retry" flow
    feed a user-corrected value back into the rerun.
    """
    from copy import deepcopy
    from core.ai_test_data import value_for_field

    overrides_map = dict(auto_fill_overrides or {})

    outcome = await replay_recording(
        recording,
        data_overrides=data_overrides,
        storage_state=storage_state,
        headless=headless,
        screenshot_dir=screenshot_dir,
        element_timeout_ms=element_timeout_ms,
        ai_matcher=ai_matcher,
        healing_enabled=healing_enabled,
        recording_path=recording_path,
        promote_on_pass=promote_on_pass,
        force_runner_up=force_runner_up,
    )
    outcome.auto_filled_fields = []

    if not outcome.new_required_fields_detected:
        return outcome

    # Proactive: scenario passed end-to-end but pre-submit diff flagged
    # unfilled required fields. No rerun — just surface the AI suggestion.
    if outcome.failed_step_index is None:
        proactive_fills: list[dict] = []
        for nr in outcome.new_required_fields_detected:
            fp_dict = nr["fingerprint"]
            attrs = fp_dict.get("attributes") or {}
            fp_id = fp_dict["id"]
            value = overrides_map.get(fp_id) or value_for_field(attrs)
            proactive_fills.append({
                "fingerprint_id": fp_id,
                "value": value,
                "attributes": attrs,
                "primary_locator": fp_dict["primary_locator"],
                "fallback_locators": fp_dict.get("fallback_locators", []),
                "source": (
                    "user_override" if fp_id in overrides_map
                    else "ai_test_data.value_for_field"
                ),
                "was_filled_in_run": False,
                "insert_before_step_index": nr.get("submit_step_index", 0),
                "discovery": nr.get("discovery", "pre_submit_diff"),
                "is_required": bool(nr.get("is_required", False)),
            })
        outcome.auto_filled_fields = proactive_fills
        return outcome

    # Reactive: failure path. Rerun with fill-steps inserted before submit.
    retry_rec = deepcopy(recording)
    failed_idx = outcome.failed_step_index
    # The failing step is the one AFTER the submit. We want to insert before
    # the submit step itself — i.e. failed_idx - 1.
    insert_at = max(0, failed_idx - 1)
    auto_fills: list[dict] = []
    for nr in outcome.new_required_fields_detected:
        fp_dict = nr["fingerprint"]
        attrs = fp_dict.get("attributes") or {}
        fp_id = fp_dict["id"]
        value = overrides_map.get(fp_id) or value_for_field(attrs)
        new_step = Step(
            index=0,  # rewritten below
            action="fill",
            value=value,
            element=ElementFingerprint.from_dict({
                "id": fp_id,
                "primary_locator": fp_dict["primary_locator"],
                "fallback_locators": fp_dict.get("fallback_locators", []),
                "attributes": attrs,
                "page_context": fp_dict.get("page_context", {}),
            }),
            inserted_by="auto-heal",
        )
        retry_rec.steps.insert(insert_at, new_step)
        insert_at += 1
        auto_fills.append({
            "fingerprint_id": fp_id,
            "value": value,
            "attributes": attrs,
            "primary_locator": fp_dict["primary_locator"],
            "fallback_locators": fp_dict.get("fallback_locators", []),
            "source": (
                "user_override" if fp_id in overrides_map
                else "ai_test_data.value_for_field"
            ),
            "was_filled_in_run": True,
            "insert_before_step_index": insert_at - 1,
            "discovery": nr.get("discovery", "post_submit_failure"),
            "is_required": bool(nr.get("is_required", False)),
        })
    # Renumber step indices
    for i, s in enumerate(retry_rec.steps):
        s.index = i

    retry_outcome = await replay_recording(
        retry_rec,
        data_overrides=data_overrides,
        storage_state=storage_state,
        headless=headless,
        screenshot_dir=screenshot_dir,
        element_timeout_ms=element_timeout_ms,
        ai_matcher=ai_matcher,
        healing_enabled=healing_enabled,
        recording_path=None,  # don't write the retry's heals yet — user must approve
        promote_on_pass=False,
    )
    retry_outcome.auto_filled_fields = auto_fills
    retry_outcome.original_failure = {
        "failed_step_index": failed_idx,
        "error": outcome.error,
    }
    # Preserve first-run skipped_steps — those skips were real observations
    # of removed fields, not artefacts of the retry. The retry's own
    # skipped_steps (if any) are additive.
    merged_skips = list(outcome.skipped_steps) + list(retry_outcome.skipped_steps)
    # Dedupe by (step_index, fingerprint_id)
    seen: set[tuple[int, str]] = set()
    dedup: list[dict] = []
    for s in merged_skips:
        key = (s.get("step_index", -1), s.get("fingerprint_id", ""))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(s)
    retry_outcome.skipped_steps = dedup
    return retry_outcome
