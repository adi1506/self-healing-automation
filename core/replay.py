from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from playwright.async_api import async_playwright, Page, Locator

from core.recording import ElementFingerprint, Step, Recording


class ElementNotFound(RuntimeError):
    """Raised when no locator (primary or fallback) matches a fingerprint."""


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


async def find_element_by_fingerprint(page: Page, fp: ElementFingerprint) -> Locator:
    """Try the primary locator, then each fallback. Return the first match.

    Match means count() >= 1 — we accept the first locator that resolves to at
    least one element. Callers use .first at action time. Healer integration
    is deferred.
    """
    candidates = [fp.primary_locator, *fp.fallback_locators]
    last_err: Exception | None = None
    for loc_dict in candidates:
        try:
            loc = _locator_for(page, loc_dict)
            if await loc.count() >= 1:
                return loc
        except Exception as e:
            last_err = e
            continue
    raise ElementNotFound(
        f"no locator matched for fingerprint {fp.id}; tried {len(candidates)} strategies"
        + (f"; last error: {last_err}" if last_err else "")
    )


async def execute_step(page: Page, step: Step, override: str | None) -> None:
    """Run one recorded step against `page`.

    `override` lets callers (test-case replay) substitute a different value
    for the same step without mutating the Recording. If None, the step's
    recorded value is used.
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
    loc = await find_element_by_fingerprint(page, step.element)
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
    failed_step_index: Optional[int] = None
    error: Optional[str] = None
    final_url: str = ""


async def replay_recording(
    recording: Recording,
    *,
    data_overrides: dict[str, str] | None = None,
    storage_state: dict | None = None,
    headless: bool = True,
) -> ReplayOutcome:
    """Open a context, navigate to start_url, walk every step.

    `data_overrides` maps `ElementFingerprint.id` -> override value. Used by
    test cases; falls back to each step's recorded value when absent.
    """
    overrides = data_overrides or {}
    outcome = ReplayOutcome()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx_kwargs = {}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        context = await browser.new_context(**ctx_kwargs)
        page = await context.new_page()
        try:
            await page.goto(recording.start_url)
            for step in recording.steps:
                ovr = None
                if step.element is not None:
                    ovr = overrides.get(step.element.id)
                try:
                    await execute_step(page, step, override=ovr)
                    outcome.completed_steps += 1
                except Exception as e:
                    outcome.failed_step_index = step.index
                    outcome.error = f"{type(e).__name__}: {e}"
                    break
            outcome.final_url = page.url
        finally:
            await context.close()
            await browser.close()
    return outcome
