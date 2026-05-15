from __future__ import annotations
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from core.capture import load_inject_js
from core.recording import Recording, Step, ElementFingerprint


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
        steps: list[Step] = []
        for idx, ev in enumerate(self._events):
            element = None
            fp_dict = ev.get("element")
            if fp_dict:
                element = ElementFingerprint.from_dict(fp_dict)
            steps.append(
                Step(
                    index=idx,
                    action=ev["action"],
                    element=element,
                    value=ev.get("value"),
                    timestamp_ms=int(ev.get("timestamp_ms") or 0),
                )
            )
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
