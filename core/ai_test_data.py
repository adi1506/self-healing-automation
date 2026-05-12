"""Backwards-compatible adapter — delegates LLM calls to AIService.

The DOM-constraint validation logic stays here because it's not AI logic —
the AIService produces values; AITestData decides whether the value is
acceptable for the field.
"""
from __future__ import annotations

import re
import time

from core.ai_service import get_ai_service


class AITestData:
    def __init__(self, host: str = "", model: str = ""):
        # host/model args ignored — AIService owns config now.
        self._svc = get_ai_service()
        self.host = self._svc.host
        self.model = self._svc.model

    # ---------- client passthrough so patch.object(ai.client, "generate") works
    @property
    def client(self):
        return self._svc.client

    @client.setter
    def client(self, value):
        self._svc.client = value

    # ---------- availability shim (tests set _available directly)
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

    def generate_value(
        self, field: dict, page_context: dict,
        per_field_rule: str = "", ai_context: str = "",
    ) -> str | None:
        from core.ai_prompts import build_field_value_prompt

        value = self._svc.generate_field_value(field, page_context, per_field_rule, ai_context)
        # Path A: model produced a value — validate and (on violation) retry once
        if value is not None:
            violation = self._validate_against_constraints(value, field)
            if not violation:
                return value
            feedback = violation
        else:
            # Path B: model failed to produce a parseable string — retry once
            feedback = "invalid JSON"

        base_prompt = build_field_value_prompt(field, page_context, per_field_rule, ai_context)
        retry_prompt = (
            base_prompt
            + f"\n\nYour previous answer violated: {feedback}. Try again. "
              f"Return strict JSON only."
        )
        raw = self._svc.generate_json(retry_prompt, timeout=15.0)
        if not raw:
            return None
        retry_value = raw.get("value")
        if not isinstance(retry_value, str):
            return None
        if self._validate_against_constraints(retry_value, field):
            return None
        return retry_value

    def _validate_against_constraints(self, value: str, field: dict) -> str:
        pattern = field.get("pattern") or ""
        if pattern and not re.fullmatch(pattern, value):
            return f"pattern {pattern}"
        maxlen = field.get("maxlength")
        if maxlen and isinstance(maxlen, (int, str)) and str(maxlen).isdigit():
            if len(value) > int(maxlen):
                return f"maxlength {maxlen}"
        minlen = field.get("minlength")
        if minlen and isinstance(minlen, (int, str)) and str(minlen).isdigit():
            if len(value) < int(minlen):
                return f"minlength {minlen}"
        etype = (field.get("element_type") or "").lower()
        if etype == "input-email" and "@" not in value:
            return "type_email"
        if etype == "input-number":
            try:
                n = float(value)
            except ValueError:
                return "type_number"
            for bound, op in [("min", lambda v, b: v < b), ("max", lambda v, b: v > b)]:
                b = field.get(bound)
                if b not in ("", None):
                    try:
                        if op(n, float(b)):
                            return f"{bound} {b}"
                    except (TypeError, ValueError):
                        pass
        return ""
