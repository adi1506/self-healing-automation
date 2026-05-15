from __future__ import annotations
from playwright.async_api import Page, Locator

from core.recording import ElementFingerprint


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
