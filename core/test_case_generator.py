from __future__ import annotations

import os
import re
import yaml
import exrex

AUTOCOMPLETE_REGISTRY = {
    "email": "test.user@example.com",
    "tel": "9876543210",
    "tel-national": "9876543210",
    "given-name": "John",
    "family-name": "Doe",
    "name": "John Doe",
    "username": "testuser",
    "new-password": "Passw0rd!",
    "current-password": "Passw0rd!",
    "organization": "Acme Inc",
    "street-address": "123 Main St",
    "address-line1": "123 Main St",
    "address-line2": "Apt 4B",
    "address-level2": "Springfield",
    "address-level1": "CA",
    "postal-code": "94105",
    "country": "US",
    "country-name": "United States",
    "bday": "1990-01-15",
    "url": "https://example.com",
    "cc-name": "John Doe",
    "cc-number": "4111111111111111",
    "cc-exp": "12/29",
    "cc-csc": "123",
    "cc-type": "Visa",
}


class TestCaseGenerator:
    """Heuristic + (later) AI-enriched generator of test case values for form fields.

    Layered resolution per field, first hit wins:
      L1 — explicit DOM constraints (pattern, type, min/max, options)
      L2 — autocomplete token registry
      L3 — semantic label/name dictionary
      L4 — LLM enrichment (added in a later task)
      Fallback — generic typed string honoring maxlength
    """

    def __init__(self, field_dictionary_path: str = "data/field_dictionary.yaml",
                 ai_client=None):
        self.ai_client = ai_client
        self._dictionary = self._load_dictionary(field_dictionary_path)

    def _load_dictionary(self, path: str) -> dict:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    # --------------------------------------------------------------------- L1
    def generate_value(self, field: dict) -> str:
        """Return one valid value for the given field via the layered resolver."""
        v = self._l1_dom_constraint(field)
        if v is not None:
            return v
        v = self._l2_autocomplete(field)
        if v is not None:
            return v
        v = self._l3_dictionary(field)
        if v is not None:
            return v
        # Layer 4 (LLM) added in a subsequent task
        return self._fallback(field)

    # --------------------------------------------------------------------- L2
    def _l2_autocomplete(self, field: dict) -> str | None:
        token = (field.get("autocomplete") or "").strip().lower()
        if not token:
            return None
        return AUTOCOMPLETE_REGISTRY.get(token)

    # --------------------------------------------------------------------- L3
    def _l3_dictionary(self, field: dict) -> str | None:
        haystack_parts = [
            field.get("element_name", ""),
            field.get("locator_label", ""),
            field.get("locator_name", ""),
            field.get("locator_id", ""),
            field.get("placeholder", ""),
        ]
        haystack = " ".join(p for p in haystack_parts if p).lower()
        if not haystack:
            return None
        for entry in self._dictionary.values():
            for needle in entry.get("match", []):
                if needle.lower() in haystack:
                    regex = entry.get("regex")
                    if regex:
                        try:
                            return exrex.getone(regex)
                        except Exception:
                            pass
                    return entry.get("example", "")
        return None

    def _l1_dom_constraint(self, field: dict) -> str | None:
        etype = (field.get("element_type") or "").lower()
        pattern = field.get("pattern") or ""
        if pattern:
            try:
                return exrex.getone(pattern)
            except Exception:
                pass

        if etype == "select" or etype == "radio":
            opts = self._parse_options(field.get("available_options", ""))
            return opts[0] if opts else ""

        if etype == "checkbox":
            return "checked"

        if etype == "input-email":
            return "test.user@example.com"

        if etype == "input-number" or etype == "input-range":
            lo = self._to_number(field.get("min"))
            hi = self._to_number(field.get("max"))
            if lo is not None and hi is not None:
                return str((lo + hi) // 2 if isinstance(lo, int) and isinstance(hi, int) else (lo + hi) / 2)
            if lo is not None:
                return str(lo)
            if hi is not None:
                return str(hi)
            return "42"

        if etype == "input-tel":
            return "9876543210"

        if etype == "input-date":
            return "2000-01-15"

        if etype == "input-url":
            return "https://example.com"

        return None

    # ---------------------------------------------------------------- helpers
    def _parse_options(self, raw: str) -> list[str]:
        return [o.strip() for o in (raw or "").split(",") if o.strip()]

    def _to_number(self, val):
        if val in (None, ""):
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

    def _fallback(self, field: dict) -> str:
        maxlen = self._to_number(field.get("maxlength"))
        base = "Test 1234"
        if maxlen and maxlen < len(base):
            return base[:maxlen]
        return base
