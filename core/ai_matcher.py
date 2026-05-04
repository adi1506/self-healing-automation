from __future__ import annotations

import json
import ollama


class AIMatcher:
    def __init__(self, host: str = "", model: str = "mistral"):
        self.host = host
        self.model = model
        self.client = ollama.Client(host=host) if host else ollama.Client()
        self._available = None

    def is_available(self) -> bool:
        """Check if Ollama is reachable and the configured model is pullable."""
        if self._available is not None:
            return self._available
        try:
            self.client.list()
            self._available = True
        except Exception:
            self._available = False
        return self._available

    def match_element(self, old_element: dict, candidates: list[dict]) -> dict | None:
        """
        Use Ollama (Mistral) to find the best match for an old element among candidates.
        Returns dict with match_index, confidence, reasoning — or None if API fails.
        """
        if not self.is_available():
            return None

        prompt = self._build_prompt(old_element, candidates)

        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                format="json",
                options={"temperature": 0.0},
            )
            return self._parse_response(response.get("response", ""))
        except Exception:
            return None

    def _build_prompt(self, old_element: dict, candidates: list[dict]) -> str:
        """Build the prompt for the LLM."""
        candidates_text = ""
        for i, c in enumerate(candidates):
            candidates_text += (
                f"  Index {i}: name='{c.get('element_name', '')}', "
                f"type='{c.get('element_type', '')}', "
                f"placeholder='{c.get('placeholder', '')}', "
                f"label='{c.get('locator_label', '')}'\n"
            )

        return f"""You are a test automation assistant. An element on a web page has changed and we need to find its new version.

The OLD element had these properties:
  name='{old_element.get('element_name', '')}'
  type='{old_element.get('element_type', '')}'
  placeholder='{old_element.get('placeholder', '')}'
  label='{old_element.get('locator_label', '')}'

These are the CURRENT unmatched elements on the page:
{candidates_text}
Which current element (by index) is most likely the same field as the old element?
Consider semantic meaning, not just exact text matches. For example, "First Name" and "Given Name" are the same field.

Respond ONLY with valid JSON in this exact format:
{{"match_index": <index or -1 if no match>, "confidence": <0.0 to 1.0>, "reasoning": "<brief explanation>"}}
"""

    def _parse_response(self, response_text: str) -> dict:
        """Parse the JSON response from the LLM."""
        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        return json.loads(text)

    def suggest_recipe(
        self, page_url: str, elements: list[dict], goal: str
    ) -> dict | None:
        """
        Ask Mistral for a draft recipe step list to achieve `goal` on `page_url`.
        Returns {"steps": [{action, target, value?}], "reasoning": str} or None.
        Steps reference elements by index in the prompt; we resolve to element_name here.
        """
        if not self.is_available():
            return None

        prompt = self._build_recipe_prompt(page_url, elements, goal)
        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                format="json",
                options={"temperature": 0.0},
            )
            raw = self._parse_response(response.get("response", ""))
        except Exception:
            return None

        if not isinstance(raw, dict) or "steps" not in raw:
            return None

        resolved_steps = []
        for step in raw["steps"]:
            idx = step.get("element_index")
            action = step.get("action")
            if action in ("wait_for_url", "wait_for_selector"):
                resolved_steps.append(step)
                continue
            if not isinstance(idx, int) or idx < 0 or idx >= len(elements):
                continue
            target_name = elements[idx].get("element_name", "")
            new_step = {"action": action, "target": target_name}
            if "value" in step:
                new_step["value"] = step["value"]
            resolved_steps.append(new_step)

        return {"steps": resolved_steps, "reasoning": raw.get("reasoning", "")}

    def _build_recipe_prompt(
        self, page_url: str, elements: list[dict], goal: str
    ) -> str:
        listing = ""
        for i, e in enumerate(elements):
            listing += (
                f"  Index {i}: name='{e.get('element_name', '')}', "
                f"type='{e.get('element_type', '')}', "
                f"placeholder='{e.get('placeholder', '')}', "
                f"label='{e.get('locator_label', '')}'\n"
            )
        return f"""You are a test automation assistant.
Goal: {goal}
Page URL: {page_url}
Available elements on this page (refer to them by INDEX only):
{listing}
Output a JSON list of steps to achieve the goal. Each step must reference
an element by INDEX from the list above. Allowed actions: fill, click, select, check.

For sensitive fields (passwords, OTPs, credit cards), use the placeholder
"<USER_FILLS>" as the value.

Respond ONLY with valid JSON in this exact format:
{{"steps": [{{"action": "fill", "element_index": 0, "value": "..."}}], "reasoning": "<brief>"}}
"""
