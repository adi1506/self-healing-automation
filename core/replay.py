from __future__ import annotations
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional
from playwright.async_api import async_playwright, Page, Locator

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
    """
    action: str = ""
    ai_matcher: object | None = None
    cache: dict[str, HealDecision] = field(default_factory=dict)
    last_decision: Optional[HealDecision] = None
    force_runner_up: dict[str, int] = field(default_factory=dict)
    pre_submit_snapshot: dict[int, list[dict]] = field(default_factory=dict)


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


def _is_step_skippable(step: Step) -> bool:
    """Decide whether a missing-element failure on this step is safe to skip.

    Rule:
      - click/submit/navigate/wait/press -> never skippable (flow-advancing)
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
    if step.action in ("click", "submit", "navigate", "wait", "press"):
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
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
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

    if step.action == "fill":
        await loc.first.fill(value or "")
    elif step.action == "click" or step.action == "submit":
        await loc.first.click()
    elif step.action == "select":
        await loc.first.select_option(value or "")
    elif step.action == "check":
        await loc.first.check()
    elif step.action == "uncheck":
        await loc.first.uncheck()
    elif step.action == "press":
        await loc.first.press(value or "")
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
    insert_before_step_index: int,
) -> None:
    """Insert AI-suggested fill steps into the recording on the scenario,
    persist the scenario. Each inserted step is marked inserted_by='auto-heal'."""
    from core.recording import Recording, Step, ElementFingerprint
    from core.scenarios import save_scenario

    rec_dict = next(
        (r for r in scenario.recordings if r.get("id") == recording_id), None,
    )
    if rec_dict is None:
        return
    rec = Recording.from_dict(rec_dict)
    insert_at = max(0, insert_before_step_index)
    for af in auto_filled:
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
        insert_at += 1
    for i, s in enumerate(rec.steps):
        s.index = i
    for i, r in enumerate(scenario.recordings):
        if r.get("id") == recording_id:
            scenario.recordings[i] = rec.to_dict()
            break
    save_scenario("data/scenarios", scenario)


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
                    result["status"] = "failed"
                    result["error"] = f"{type(e).__name__}: {e}"
                    # Surface the healer's diagnostic on the failed step
                    # even when no heal was committed — explains why the
                    # best candidate wasn't picked.
                    if heal_context is not None and heal_context.last_decision is not None:
                        d = heal_context.last_decision
                        result["heal_diagnostics"] = d.diagnostics
                    outcome.failed_step_index = step.index
                    outcome.error = f"{type(e).__name__}: {e}"

                    # Post-submit error scan: if a step just before this one
                    # was a submit-like action, scan for new error messages
                    # and identify required fields the recording didn't fill.
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
) -> ReplayOutcome:
    """Wrap replay_recording with an auto-retry path: if the first attempt
    fails and `new_required_fields_detected` is non-empty, AI-fill those
    fields and re-run with them inserted before the failing submit step.
    The outcome.auto_filled_fields list records what was filled so the UI
    can offer 'Save this step.'"""
    from copy import deepcopy
    from core.ai_test_data import value_for_field

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

    if outcome.failed_step_index is None or not outcome.new_required_fields_detected:
        # No retry needed — either it passed or the failure isn't a missing field.
        return outcome

    # Build an extended recording with inserted fill-steps before the failing
    # submit step.
    retry_rec = deepcopy(recording)
    failed_idx = outcome.failed_step_index
    # The failing step is the one AFTER the submit. We want to insert before
    # the submit step itself — i.e. failed_idx - 1.
    insert_at = max(0, failed_idx - 1)
    auto_fills: list[dict] = []
    for nr in outcome.new_required_fields_detected:
        fp_dict = nr["fingerprint"]
        attrs = fp_dict.get("attributes") or {}
        value = value_for_field(attrs)
        new_step = Step(
            index=0,  # rewritten below
            action="fill",
            value=value,
            element=ElementFingerprint.from_dict({
                "id": fp_dict["id"],
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
            "fingerprint_id": fp_dict["id"],
            "value": value,
            "attributes": attrs,
            "primary_locator": fp_dict["primary_locator"],
            "fallback_locators": fp_dict.get("fallback_locators", []),
            "source": "ai_test_data.value_for_field",
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
    return retry_outcome
