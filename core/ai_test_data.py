from __future__ import annotations

import json
import os
import re

try:
    import ollama
except ImportError:
    ollama = None


class AITestData:
    """Per-cell LLM enrichment for the test case generator.

    Calls Ollama (Mistral by default) one field at a time with `format=json`,
    validates the returned value against the field's DOM constraints, retries
    once on failure, and returns None if it can't produce a valid value.
    """

    def __init__(self, host: str = "", model: str = ""):
        self.host = host or os.environ.get("OLLAMA_HOST", "")
        self.model = model or os.environ.get("OLLAMA_MODEL", "mistral")
        if ollama is not None:
            self.client = ollama.Client(host=self.host) if self.host else ollama.Client()
        else:
            self.client = None
        self._available = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        if self.client is None:
            self._available = False
            return False
        try:
            self.client.list()
            self._available = True
        except Exception:
            self._available = False
        return self._available

    def generate_value(
        self,
        field: dict,
        page_context: dict,
        per_field_rule: str = "",
        ai_context: str = "",
    ) -> str | None:
        if not self.is_available():
            return None

        prompt = self._build_prompt(field, page_context, per_field_rule, ai_context)
        value, violation = self._call_and_validate(prompt, field)
        if value is not None:
            return value
        # Retry once with feedback
        retry_prompt = (
            prompt
            + f"\n\nYour previous answer violated: {violation}. Try again. "
              f"Return strict JSON only."
        )
        value, _ = self._call_and_validate(retry_prompt, field)
        return value

    def _call_and_validate(self, prompt: str, field: dict) -> tuple[str | None, str]:
        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                format="json",
                options={"temperature": 0.0},
            )
        except Exception:
            return None, "ollama call failed"
        try:
            payload = json.loads(response.get("response", ""))
        except (ValueError, TypeError):
            return None, "invalid JSON"
        value = payload.get("value")
        if not isinstance(value, str):
            return None, "value not a string"
        violation = self._validate_against_constraints(value, field)
        if violation:
            return None, violation
        return value, ""

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

    def _build_prompt(
        self, field: dict, page_context: dict, per_field_rule: str, ai_context: str
    ) -> str:
        constraints = self._summarize_constraints(field)
        ctx_line = ". ".join(
            v for v in (page_context.get("title", ""),
                        page_context.get("h1", ""),
                        page_context.get("first_paragraph", "")) if v
        ) or "none"
        return (
            "You are generating one value for a single form field.\n"
            f"Page context: {ctx_line}\n"
            f"Field label: {field.get('locator_label') or field.get('element_name', '')}\n"
            f"Field name: {field.get('locator_name', '')}\n"
            f"Field type: {field.get('element_type', '')}\n"
            f"Helper text: {field.get('helper_text') or 'none'}\n"
            f"DOM constraints: {constraints or 'none'}\n"
            f"Per-field rule: {per_field_rule or 'none'}\n"
            f"Test case scenario: {ai_context or 'default valid value'}\n"
            "Return strict JSON only: {\"value\": \"<generated value>\"}"
        )

    def _summarize_constraints(self, field: dict) -> str:
        parts = []
        if field.get("pattern"): parts.append(f"pattern={field['pattern']}")
        if field.get("maxlength"): parts.append(f"maxlength={field['maxlength']}")
        if field.get("minlength"): parts.append(f"minlength={field['minlength']}")
        if field.get("min") not in ("", None): parts.append(f"min={field['min']}")
        if field.get("max") not in ("", None): parts.append(f"max={field['max']}")
        if field.get("required"): parts.append("required")
        if field.get("autocomplete"): parts.append(f"autocomplete={field['autocomplete']}")
        return ", ".join(parts)
