"""Single owner of all Ollama interactions.

- Resolves host/model from data/settings.yaml > env vars > defaults.
- Caches is_available() for 30s.
- Exposes a singleton via get_ai_service().
"""
from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
import yaml

try:
    import ollama
except ImportError:
    ollama = None


DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "phi4:14b"
DEFAULT_SETTINGS_PATH = "data/settings.yaml"
AVAILABILITY_TTL_SEC = 30.0


class AIService:
    def __init__(self, settings_path: str = DEFAULT_SETTINGS_PATH):
        self.settings_path = settings_path
        self.host = DEFAULT_HOST
        self.model = DEFAULT_MODEL
        self._load_config()
        self.client = ollama.Client(host=self.host) if ollama is not None else None
        self._available = None
        self._available_at = 0.0
        self.last_error: str | None = None
        self.last_latency_ms: float | None = None
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ai-svc")
        self._cache: dict[str, dict] = {}

    # ---------------------------------------------------------------- config
    def _load_config(self) -> None:
        # Order: settings.yaml > env > default
        env_host = os.environ.get("OLLAMA_HOST", "").strip()
        env_model = os.environ.get("OLLAMA_MODEL", "").strip()
        if env_host:
            self.host = env_host
        if env_model:
            self.model = env_model

        path = Path(self.settings_path)
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                ai = data.get("ai") or {}
                if ai.get("host"):
                    self.host = str(ai["host"])
                if ai.get("model"):
                    self.model = str(ai["model"])
            except Exception as e:
                self.last_error = f"settings.yaml read failed: {e}"

    def save_config(self, *, host: str, model: str) -> None:
        """Persist host/model to settings.yaml and reload."""
        path = Path(self.settings_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"ai": {"host": host, "model": model}}
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        self.reload()

    def reload(self) -> None:
        """Re-read settings, reset client, clear availability cache."""
        self._load_config()
        self.client = ollama.Client(host=self.host) if ollama is not None else None
        self._available = None
        self._available_at = 0.0
        self._cache.clear()

    # ---------------------------------------------------------- availability
    def is_available(self) -> bool:
        if self.client is None:
            return False
        if self._available is not None and (time.monotonic() - self._available_at) < AVAILABILITY_TTL_SEC:
            return self._available
        try:
            self.client.list()
            self._available = True
            self.last_error = None
        except Exception as e:
            self._available = False
            self.last_error = str(e)
        self._available_at = time.monotonic()
        return self._available

    # -------------------------------------------------------------- primitive
    def generate_json(self, prompt: str, *, timeout: float = 30.0,
                      cache_key: tuple | None = None) -> dict | None:
        """Generate a JSON response from the model.

        - Sets format=json and temperature=0.0.
        - Enforces per-call timeout via the shared executor.
        - Strips <think>...</think> and ```json fences before parsing.
        - Returns None on unavailable / timeout / invalid JSON.
        - cache_key is a no-op here; Phase 4 Task 12 wires it.
        """
        if self.client is None or not self.is_available():
            return None
        from core.ai_prompts import PROMPT_VERSION
        composite_key: str | None = None
        if cache_key is not None:
            payload = repr((cache_key, self.model, PROMPT_VERSION))
            composite_key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            cached = self._cache.get(composite_key)
            if cached is not None:
                return cached
        start = time.monotonic()

        def _call():
            return self.client.generate(
                model=self.model, prompt=prompt,
                format="json", options={"temperature": 0.0},
            )

        future = self._executor.submit(_call)
        try:
            response = future.result(timeout=timeout)
        except FuturesTimeoutError:
            future.cancel()
            try:
                # Close the underlying HTTP connection so the worker thread can unblock.
                inner = getattr(self.client, "_client", None)
                if inner is not None and hasattr(inner, "close"):
                    inner.close()
            except Exception:
                pass
            self.last_error = f"timeout after {timeout}s"
            self.last_latency_ms = (time.monotonic() - start) * 1000.0
            return None
        except Exception as e:
            self.last_error = f"generate failed: {e}"
            self.last_latency_ms = (time.monotonic() - start) * 1000.0
            return None

        self.last_latency_ms = (time.monotonic() - start) * 1000.0
        raw = response.get("response", "") if isinstance(response, dict) else ""
        parsed = self._parse_json_response(raw)
        if parsed is not None and composite_key is not None:
            # Bound the cache at 512 entries (drop oldest entry on overflow).
            if len(self._cache) >= 512:
                oldest = next(iter(self._cache))
                self._cache.pop(oldest, None)
            self._cache[composite_key] = parsed
        return parsed

    @staticmethod
    def _parse_json_response(raw: str) -> dict | None:
        import json
        import re
        text = raw.strip()
        # Strip <think>...</think> blocks (Qwen3 etc.)
        text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
        # Strip ```json ... ``` code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0]
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None

    # ----------------------------------------------------------- high level
    def match_element(self, old_element: dict, candidates: list[dict]) -> dict | None:
        from core.ai_prompts import build_match_prompt
        prompt = build_match_prompt(old_element, candidates)
        cache_key = (
            "match",
            old_element.get("element_name", ""),
            old_element.get("element_type", ""),
            old_element.get("placeholder", ""),
            old_element.get("locator_label", ""),
            tuple(
                (c.get("element_name", ""), c.get("element_type", ""),
                 c.get("placeholder", ""), c.get("locator_label", ""))
                for c in candidates
            ),
        )
        return self.generate_json(prompt, timeout=15.0, cache_key=cache_key)

    def suggest_recipe(self, page_url: str, elements: list[dict],
                       goal: str) -> dict | None:
        from core.ai_prompts import build_recipe_prompt
        prompt = build_recipe_prompt(page_url, elements, goal)
        raw = self.generate_json(prompt, timeout=45.0)
        if not isinstance(raw, dict) or "steps" not in raw:
            return None
        resolved_steps = []
        for step in raw["steps"]:
            action = step.get("action")
            if action in ("wait_for_url", "wait_for_selector"):
                resolved_steps.append(step)
                continue
            idx = step.get("element_index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(elements):
                continue
            target_name = elements[idx].get("element_name", "")
            new_step = {"action": action, "target": target_name}
            if "value" in step:
                new_step["value"] = step["value"]
            resolved_steps.append(new_step)
        return {"steps": resolved_steps, "reasoning": raw.get("reasoning", "")}

    def generate_field_value(
        self, field: dict, page_context: dict,
        per_field_rule: str = "", ai_context: str = "",
    ) -> str | None:
        """Generate one value for a single form field via the LLM. Constraint
        validation is performed by the AITestData adapter caller, not here.
        Returns the raw value string or None on any failure.
        """
        from core.ai_prompts import build_field_value_prompt
        prompt = build_field_value_prompt(field, page_context, per_field_rule, ai_context)
        cache_key = (
            "field_value",
            field.get("element_name", ""),
            page_context.get("title", ""),
            per_field_rule,
            ai_context,
        )
        raw = self.generate_json(prompt, timeout=15.0, cache_key=cache_key)
        if not raw:
            return None
        val = raw.get("value")
        return val if isinstance(val, str) else None

    def refine_row(
        self, field_defs: list[dict], current_row: dict[str, str],
        refine_prompt: str,
    ) -> dict[str, str] | None:
        """Return a new row that respects the user instruction while leaving
        DOM-constrained fields untouched.
        """
        if self.client is None or not self.is_available():
            return None
        from core.ai_prompts import build_refine_row_prompt
        locked = [
            f.get("element_name", "") for f in field_defs
            if self._is_locked_field(f)
        ]
        prompt = build_refine_row_prompt(field_defs, current_row, refine_prompt, locked)
        raw = self.generate_json(prompt, timeout=30.0)
        if not raw or not isinstance(raw.get("values"), dict):
            return None
        out: dict[str, str] = {}
        for f in field_defs:
            name = f.get("element_name", "")
            if not name:
                continue
            if name in locked:
                out[name] = current_row.get(name, "")
            else:
                model_val = raw["values"].get(name)
                out[name] = model_val if isinstance(model_val, str) else current_row.get(name, "")
        return out

    @staticmethod
    def _is_locked_field(field: dict) -> bool:
        """A field is locked from AI refinement if it has a DOM constraint."""
        etype = (field.get("element_type") or "").lower()
        if etype in ("select", "radio", "checkbox"):
            return True
        return bool(
            field.get("pattern")
            or field.get("min") not in ("", None)
            or field.get("max") not in ("", None)
            or field.get("maxlength")
            or field.get("minlength")
        )


# --------------------------------------------------------------------- singleton
_service: AIService | None = None


def get_ai_service(settings_path: str = DEFAULT_SETTINGS_PATH) -> AIService:
    global _service
    if _service is None:
        _service = AIService(settings_path=settings_path)
    return _service


def reset_ai_service() -> None:
    """Test helper — drop the singleton so a fresh one is built next call."""
    global _service
    _service = None
