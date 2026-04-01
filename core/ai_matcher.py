import json
import google.generativeai as genai


class AIMatcher:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        if api_key:
            genai.configure(api_key=api_key)

    def is_available(self) -> bool:
        """Check if the AI matcher is available (has API key)."""
        return bool(self.api_key)

    def match_element(self, old_element: dict, candidates: list[dict]) -> dict | None:
        """
        Use Gemini to find the best match for an old element among candidates.
        Returns dict with match_index, confidence, reasoning — or None if API fails.
        """
        if not self.is_available():
            return None

        prompt = self._build_prompt(old_element, candidates)

        try:
            model = genai.GenerativeModel("gemini-2.0-flash")
            response = model.generate_content(prompt)
            return self._parse_response(response.text)
        except Exception:
            return None

    def _build_prompt(self, old_element: dict, candidates: list[dict]) -> str:
        """Build the prompt for Gemini."""
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
        """Parse the JSON response from Gemini."""
        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        return json.loads(text)
