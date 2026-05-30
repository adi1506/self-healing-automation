"""Flutter-fixture harness: record/replay against local flt-semantics HTML.

Mirrors tests/dogfood/harness.py, but instead of the live netlify site it
fulfills a FIXED URL with a chosen local fixture's HTML. Using one stable
URL for every variant is required: core.replay_healer.urls_compatible
compares host+path, so serving v1 and v2 from different file paths would
make every heal a URL-context mismatch. Route interception keeps the URL
constant while swapping the body.
"""
from __future__ import annotations
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from playwright.async_api import async_playwright as _real_apw, BrowserContext

from core.capture import load_inject_js
from core.recording import Recording, save_recording
from core.recorder import RecorderSession
from core import replay as replay_mod

FIXED_URL = "http://flutter.fixture.test/app"
_FIXTURES = _ROOT / "tests" / "fixtures"
_VARIANT_FILE = {
    "v1": _FIXTURES / "flutter_v1.html",
    "v2": _FIXTURES / "flutter_v2_relabel.html",
    "v3": _FIXTURES / "flutter_v3_swap.html",
}


async def _install_fixture_route(ctx: BrowserContext, variant: str) -> None:
    """Fulfill the top-level document at FIXED_URL with the variant's HTML."""
    html = _VARIANT_FILE[variant].read_text(encoding="utf-8")

    async def handler(route):
        req = route.request
        if req.url.rstrip("/") == FIXED_URL.rstrip("/"):
            await route.fulfill(status=200, content_type="text/html", body=html)
        else:
            await route.continue_()

    await ctx.route("**/*", handler)


class _FlutterRecorderSession(RecorderSession):
    """RecorderSession that fulfills FIXED_URL with a fixture before goto."""

    def __init__(self, *args, variant: str = "v1", **kwargs):
        super().__init__(*args, **kwargs)
        self._variant = variant

    async def start(self, start_url: str) -> None:
        self._start_url = start_url
        self._start_ts = time.time()
        self._pw = await _real_apw().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        ctx_kwargs = {}
        if self.storage_state:
            ctx_kwargs["storage_state"] = self.storage_state
        self._context = await self._browser.new_context(**ctx_kwargs)
        await _install_fixture_route(self._context, self._variant)
        await self._context.add_init_script(load_inject_js())
        self.page = await self._context.new_page()
        await self.page.expose_function("__sha_record", self._on_event)
        await self.page.goto(start_url)
        await self.page.evaluate("() => window.__sha && window.__sha.attachListeners()")


async def _drive_flutter_v1(page) -> None:
    """Click the Individual radio, fill Mobile No, click the chevron.

    Uses the data-truth attribute to target nodes unambiguously at RECORD
    time. The recorder captures Flutter fingerprints (ordinal ids, etc.);
    these data-truth hooks are only for driving and for ground-truth
    assertions, never used by the recorder/healer themselves.
    """
    await page.wait_for_selector("flt-semantics-host", timeout=10000)
    await page.click('flt-semantics[data-truth="radio-individual"]')
    await page.fill('input[data-truth="mobile"]', "5551234567")
    await page.click('flt-semantics[data-truth="chevron"]')
    await page.wait_for_timeout(300)


async def record_flutter(scratch_dir: str, variant: str = "v1",
                         name: str = "flutter") -> Recording:
    """Record the v1 happy path. Saves to scratch_dir/recording.yaml."""
    session = _FlutterRecorderSession(
        application_id="flutter-fixture-app", headless=True, variant=variant)
    await session.start(start_url=FIXED_URL)
    try:
        await _drive_flutter_v1(session.page)
    finally:
        rec = await session.stop(name=name)
    out_path = Path(scratch_dir) / "recording.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_recording(str(out_path), rec)
    return rec


# --- Replay with fixed-URL fulfillment (mirror of harness._PWWrapper) ------

class _PWWrapper:
    def __init__(self, variant: str):
        self._variant = variant
        self._pw = None

    async def __aenter__(self):
        self._pw = await _real_apw().start()
        return _PWProxy(self._pw, self._variant)

    async def __aexit__(self, *a):
        await self._pw.stop()


class _PWProxy:
    def __init__(self, pw, variant):
        self._pw = pw
        self._variant = variant

    def __getattr__(self, name):
        if name == "chromium":
            return _BrowserTypeProxy(self._pw.chromium, self._variant)
        return getattr(self._pw, name)


class _BrowserTypeProxy:
    def __init__(self, bt, variant):
        self._bt = bt
        self._variant = variant

    async def launch(self, **kwargs):
        return _BrowserProxy(await self._bt.launch(**kwargs), self._variant)


class _BrowserProxy:
    def __init__(self, browser, variant):
        self._browser = browser
        self._variant = variant

    async def new_context(self, **kwargs):
        ctx = await self._browser.new_context(**kwargs)
        await _install_fixture_route(ctx, self._variant)
        return ctx

    async def close(self):
        await self._browser.close()


@contextmanager
def count_heal_attempts():
    """Wrap core.replay.attempt_heal to count invocations.

    attempt_heal is the full scan+score heal pass that fires whenever the
    fast path misses. Counting it is the headline speed metric (spec §3.2);
    no such counter exists in production code. Yields a one-key dict whose
    'n' is updated in place.
    """
    counter = {"n": 0}
    original = replay_mod.attempt_heal

    async def _wrapped(*args, **kwargs):
        counter["n"] += 1
        return await original(*args, **kwargs)

    replay_mod.attempt_heal = _wrapped
    try:
        yield counter
    finally:
        replay_mod.attempt_heal = original


async def replay_flutter(recording: Recording, variant: str, scratch_dir: str,
                         *, recording_path: Optional[str] = None):
    """Replay `recording` against the given fixture variant. Returns
    (ReplayOutcome, heal_attempt_count)."""
    original = replay_mod.async_playwright
    replay_mod.async_playwright = lambda: _PWWrapper(variant)
    try:
        with count_heal_attempts() as counter:
            outcome = await replay_mod.replay_recording(
                recording,
                recording_path=recording_path,
                headless=True,
                healing_enabled=True,
                screenshot_dir=str(Path(scratch_dir) / "screenshots"),
            )
        return outcome, counter["n"]
    finally:
        replay_mod.async_playwright = original


def element_step_count(recording: Recording) -> int:
    """Number of steps that carry an element (i.e. reach the heal path)."""
    return sum(1 for s in recording.steps if s.element is not None)
