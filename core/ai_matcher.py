"""Backwards-compatible adapter — delegates to core.ai_service.AIService.

Public API preserved so core/healer.py and tests don't break.
"""
from __future__ import annotations

import time

from core.ai_service import get_ai_service


class AIMatcher:
    def __init__(self, host: str = "", model: str = ""):
        # host/model args ignored — AIService owns config now. Kept for
        # backwards compatibility with existing callers.
        self._svc = get_ai_service()
        self.host = self._svc.host
        self.model = self._svc.model

    # -------- client passthrough: tests assign matcher.client = MagicMock();
    # we forward to the singleton so the patched client is the one actually used.
    @property
    def client(self):
        return self._svc.client

    @client.setter
    def client(self, value):
        self._svc.client = value

    # -------- availability cache shim (preserve _available attr for tests) ----
    @property
    def _available(self):
        return self._svc._available

    @_available.setter
    def _available(self, value):
        self._svc._available = value
        if value is not None:
            self._svc._available_at = time.monotonic()

    def is_available(self) -> bool:
        return self._svc.is_available()

    def match_element(self, old_element: dict, candidates: list[dict]) -> dict | None:
        return self._svc.match_element(old_element, candidates)

    def suggest_recipe(self, page_url: str, elements: list[dict],
                       goal: str) -> dict | None:
        return self._svc.suggest_recipe(page_url, elements, goal)
