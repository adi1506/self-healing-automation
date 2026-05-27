from __future__ import annotations
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from core.capture import load_inject_js
from core.recording import Recording, Step, ElementFingerprint


# Fields whose value cannot be meaningfully replayed: captcha, OTP, 2FA codes,
# security questions. Steps targeting these are flagged needs_manual=True so
# replay pauses for a human. Auto-detection only — user can flip the flag in
# the recording editor for false positives / negatives.
#
# We tokenize the field's identifiers (name/id/placeholder/label) so camelCase,
# snake_case, and kebab-case all decompose to space-separated lowercase tokens
# before matching: 'enteredCaptcha' -> 'entered captcha', 'otp_code' -> 'otp
# code'. Then a substring check against the keyword list catches all variants.
_MANUAL_KEYWORDS = (
    "captcha", "otp", "2fa", "verif",
    "security code", "security question", "security answer",
)


def _tokenize_field_id(s: str) -> str:
    # camelCase / PascalCase → split at lower→upper boundaries.
    # Intentionally NOT splitting digit→upper so '2FA' stays one token after
    # lowercasing (the digit-letter pair carries meaning, e.g. '2fa', 'g2fa').
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    # Any non-alphanumeric run becomes a single space
    s = re.sub(r"[^a-zA-Z0-9]+", " ", s)
    return s.lower().strip()


def _looks_like_manual_field(element: ElementFingerprint | None) -> bool:
    if element is None:
        return False
    attrs = element.attributes or {}
    blob = " ".join(
        _tokenize_field_id(str(attrs.get(k, "") or ""))
        for k in ("name", "id", "placeholder", "aria_label", "nearest_label_text", "autocomplete")
    )
    return any(kw in blob for kw in _MANUAL_KEYWORDS)


_VALUE_EMITTING_FOLLOWUPS = frozenset({"fill", "select", "check", "uncheck"})


def _coalesce_field_clicks(events: list[dict]) -> list[dict]:
    """Drop `click` events immediately followed by a value-emitting event
    (`fill`/`select`/`check`/`uncheck`) on the SAME physical element.

    The capture script records a click on every interactive element, so
    typing into an `<input>` or picking from a `<select>` produces both
    a `click` and a `fill`/`select` step on the same element. The click
    is noise — it's not what the user meant to express — and worse, it
    breaks healing on schema changes because `is_action_compatible` for
    action="click" rejects `<select>` / `<input type=text>` candidates,
    so the healer can't relocate the field for the stray click step.

    Coalescing rule (intentionally conservative):
      - Pair (event[i], event[i+1])
      - event[i].action == "click"
      - event[i+1].action in {fill, select, check, uncheck}
      - Both events carry an element fingerprint with the same raw `id`
        (the per-page `el-N` minted by inject.js, BEFORE per-step
        `-sN` suffixing)
      → drop event[i]

    Same-element check uses the raw id, not the primary locator, because
    the same physical element always gets the same fp.id within a page
    (inject.js dedups by xpath + neighborhood signature). Click+click on
    different elements (combobox trigger → option in flyout) is left
    untouched: different fp.ids, so the pair doesn't match.
    """
    if not events:
        return events
    out: list[dict] = []
    i = 0
    while i < len(events):
        cur = events[i]
        nxt = events[i + 1] if i + 1 < len(events) else None
        if (
            nxt is not None
            and cur.get("action") == "click"
            and nxt.get("action") in _VALUE_EMITTING_FOLLOWUPS
        ):
            cur_el = cur.get("element") or {}
            nxt_el = nxt.get("element") or {}
            cur_id = cur_el.get("id")
            nxt_id = nxt_el.get("id")
            if cur_id and cur_id == nxt_id:
                # Drop cur (the click); keep nxt by falling through to it
                # on the next loop iteration.
                i += 1
                continue
        out.append(cur)
        i += 1
    return out


def _build_steps(events: list[dict]) -> list[Step]:
    # Pre-pass: drop redundant clicks on form-value elements that are
    # immediately followed by a fill/select/check/uncheck on the same
    # physical element. See `_coalesce_field_clicks` for rationale.
    events = _coalesce_field_clicks(events)

    # The injected JS resets its `el-N` counter on every page navigation, so a
    # 3-page wizard mints colliding ids across pages. Suffix each step's id
    # with its own step index so the recording-wide id space is collision-free
    # — auto-promote on a healed step can't bleed into unrelated steps that
    # happen to carry the same raw `el-N`.
    steps: list[Step] = []
    for idx, ev in enumerate(events):
        element = None
        fp_dict = ev.get("element")
        if fp_dict:
            element = ElementFingerprint.from_dict(fp_dict)
            element.id = f"{element.id}-s{idx}"
        needs_manual = (
            ev["action"] == "fill" and _looks_like_manual_field(element)
        )
        steps.append(
            Step(
                index=idx,
                action=ev["action"],
                element=element,
                value=ev.get("value"),
                timestamp_ms=int(ev.get("timestamp_ms") or 0),
                needs_manual=needs_manual,
            )
        )
    return steps


class RecorderSession:
    """Owns a headed Chromium recording session.

    Lifecycle:
        rec = RecorderSession(application_id="app-1")
        await rec.start(start_url="https://target/")
        # ... user drives the browser in real life ...
        recording = await rec.stop(name="Happy path")
    """

    def __init__(
        self,
        application_id: str,
        *,
        headless: bool = False,
        storage_state: dict | None = None,
    ):
        self.application_id = application_id
        self.headless = headless
        self.storage_state = storage_state
        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._events: list[dict] = []
        self._start_url: str = ""
        self._start_ts: float = 0.0

    async def start(self, start_url: str) -> None:
        self._start_url = start_url
        self._start_ts = time.time()
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        context_kwargs = {}
        if self.storage_state:
            context_kwargs["storage_state"] = self.storage_state
        self._context = await self._browser.new_context(**context_kwargs)
        await self._context.add_init_script(load_inject_js())
        self.page = await self._context.new_page()
        await self.page.expose_function("__sha_record", self._on_event)
        await self.page.goto(start_url)
        # The page may have loaded before expose_function landed; ensure
        # listeners are attached now that __sha_record exists.
        await self.page.evaluate("() => window.__sha && window.__sha.attachListeners()")

    def _on_event(self, payload: dict) -> None:
        self._events.append(payload)

    async def stop(self, name: str) -> Recording:
        steps = _build_steps(self._events)
        recording = Recording(
            id="rec-" + uuid.uuid4().hex[:8],
            name=name,
            kind="scenario",
            application_id=self.application_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            start_url=self._start_url,
            steps=steps,
            success_signal=None,
        )
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()
        return recording
