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

# Per-model maximum context window (tokens). Set as num_ctx so Ollama does not
# cap the window low by default and silently truncate long prompts.
MODEL_MAX_CTX = {
    "phi4:14b": 16384,
}
DEFAULT_MAX_CTX = 8192       # conservative fallback for unlisted models
MAX_CTX_CEILING = 16384      # deployment guard against over-allocation


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
            listing = self.client.list()
            installed = self._extract_model_names(listing)
            if installed and self.model not in installed:
                self._available = False
                self.last_error = (
                    f"model '{self.model}' not pulled on host (run: ollama pull {self.model})"
                )
            else:
                self._available = True
                self.last_error = None
        except Exception as e:
            self._available = False
            self.last_error = str(e)
        self._available_at = time.monotonic()
        return self._available

    def _num_ctx(self) -> int:
        """Context window to request from Ollama — the configured model's max,
        capped by a deployment ceiling."""
        return min(MODEL_MAX_CTX.get(self.model, DEFAULT_MAX_CTX), MAX_CTX_CEILING)

    @staticmethod
    def _extract_model_names(listing) -> list[str]:
        """Pull model names from either a dict or a Pydantic ListResponse."""
        models = []
        if hasattr(listing, "models"):
            models = listing.models or []
        elif isinstance(listing, dict):
            models = listing.get("models", []) or []
        names: list[str] = []
        for m in models:
            name = None
            if hasattr(m, "model"):
                name = m.model
            elif isinstance(m, dict):
                name = m.get("name") or m.get("model")
            if name:
                names.append(name)
        return names

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
                format="json",
                options={"temperature": 0.0, "num_ctx": self._num_ctx()},
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
        raw = self._extract_response_text(response)
        if not raw:
            self.last_error = (
                f"model '{self.model}' returned empty response "
                f"(response type: {type(response).__name__})"
            )
            return None
        parsed = self._parse_json_response(raw)
        if parsed is None:
            snippet = raw[:300].replace("\n", " ")
            self.last_error = f"model returned unparseable JSON: {snippet!r}"
            return None
        self.last_error = None
        if composite_key is not None:
            # Bound the cache at 512 entries (drop oldest entry on overflow).
            if len(self._cache) >= 512:
                oldest = next(iter(self._cache))
                self._cache.pop(oldest, None)
            self._cache[composite_key] = parsed
        return parsed

    @staticmethod
    def _extract_response_text(response) -> str:
        """Pull the `.response` text from either a dict or a Pydantic GenerateResponse."""
        if response is None:
            return ""
        if isinstance(response, dict):
            return response.get("response", "") or ""
        text = getattr(response, "response", None)
        return text or ""

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
        raw = self.generate_json(prompt, timeout=180.0)
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

    def suggest_test_cases_for_recording(
        self, recording, count: int, focus_areas: list[str],
        app_context: str = "",
    ) -> list[dict] | None:
        """Return `count` test-case variants for a recording, or None on failure.

        Locked steps (step.locked_value) are kept at their recorded value and
        shown to the model as fixed context — never overridable. Per-field
        context, app context, and the screen/route are threaded into the prompt.
        In regression mode, only success-outcome variants are kept.
        """
        from core.ai_prompts import build_test_cases_for_recording_prompt

        overridable: list[dict] = []
        index_to_fp: dict[int, str] = {}
        fixed_fields: list[dict] = []
        for step in recording.steps:
            if step.action not in ("fill", "select") or step.element is None:
                continue
            attrs = step.element.attributes or {}
            if getattr(step, "locked_value", False):
                label = (attrs.get("aria_label") or attrs.get("nearest_label_text")
                         or attrs.get("name") or attrs.get("id")
                         or attrs.get("text_content") or "field")
                fixed_fields.append({"label": label, "value": step.value})
                continue
            idx = len(overridable)
            index_to_fp[idx] = step.element.id
            overridable.append({
                "action": step.action,
                "value": step.value,
                "attributes": attrs,
                "field_context": getattr(step, "field_context", None),
            })
        if not overridable:
            return []

        prompt = build_test_cases_for_recording_prompt(
            overridable, count, focus_areas,
            app_context=app_context,
            screen_context=self._recording_screen_context(recording),
            fixed_fields=fixed_fields,
        )
        raw = self.generate_json(prompt, timeout=180.0)
        if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
            return None

        regression = "Regression Testing" in (focus_areas or [])
        resolved: list[dict] = []
        for case in raw["cases"]:
            if not isinstance(case, dict):
                continue
            name = (case.get("name") or "").strip()
            outcome = case.get("expected_outcome")
            if outcome not in ("success", "failure"):
                outcome = "failure"
            if regression and outcome != "success":
                continue  # regression set is valid-only
            overrides: dict[str, str] = {}
            for ov in case.get("overrides") or []:
                if not isinstance(ov, dict):
                    continue
                idx = ov.get("step_index")
                if not isinstance(idx, int) or idx not in index_to_fp:
                    continue
                overrides[index_to_fp[idx]] = str(ov.get("value", ""))
            if not name or not overrides:
                continue
            resolved.append({
                "name": name,
                "expected_outcome": outcome,
                "overrides": overrides,
                "rationale": (case.get("rationale") or "").strip(),
            })
        return resolved

    @staticmethod
    def _recording_screen_context(recording) -> str:
        """Best-effort screen label + route for the prompt header."""
        route = getattr(recording, "start_url", "") or ""
        section = ""
        for step in recording.steps:
            if step.element is None:
                continue
            section = ((step.element.attributes or {}).get("nearest_landmark_text") or "").strip()
            if section:
                break
        if section and route:
            return f"{section} ({route})"
        return section or route

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
        raw = self.generate_json(prompt, timeout=90.0)
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

    def generate_complementary_rows(
        self, field_defs: list[dict], existing_rows: list[dict],
        batch_context: str, n: int,
    ) -> list[dict[str, str]]:
        """Generate N complementary rows in parallel. Returns rows in submission
        order. Invalid model outputs are silently dropped.
        """
        if self.client is None or not self.is_available() or n <= 0:
            return []
        from core.ai_prompts import build_complementary_row_prompt

        def _one_row(position: int) -> dict | None:
            prompt = build_complementary_row_prompt(
                field_defs, existing_rows, batch_context, position,
            )
            # Phi-4 14B regularly needs 30s+ for a single JSON row; 15s was
            # silently timing out and the panel reported "No rows generated."
            raw = self.generate_json(prompt, timeout=90.0)
            if not raw or not isinstance(raw.get("values"), dict):
                return None
            row: dict[str, str] = {}
            for f in field_defs:
                name = f.get("element_name", "")
                if not name:
                    continue
                v = raw["values"].get(name, "")
                row[name] = v if isinstance(v, str) else str(v)
            # Carry the model's per-row label out under a meta key so the
            # caller can use it for the test name. Field names never start
            # with "__", so this won't collide with form data.
            ai_name = raw.get("name")
            if isinstance(ai_name, str) and ai_name.strip():
                row["__test_name"] = ai_name.strip()
            return row

        futures = [self._executor.submit(_one_row, i + 1) for i in range(n)]
        out: list[dict] = []
        for fut in futures:
            try:
                row = fut.result(timeout=100.0)
            except Exception:
                row = None
            if row is not None:
                out.append(row)
        return out

    def summarize_run(self, run_record: dict) -> str:
        if self.client is None or not self.is_available():
            return ""
        from core.ai_prompts import build_summarize_run_prompt
        prompt = build_summarize_run_prompt(run_record)
        cache_key = ("summarize", run_record.get("id", ""))
        raw = self.generate_json(prompt, timeout=30.0, cache_key=cache_key)
        if not raw or not isinstance(raw.get("summary"), str):
            return ""
        return raw["summary"]

    def suggest_scenarios(self, page: dict) -> list[dict]:
        if self.client is None or not self.is_available():
            return []
        from core.ai_prompts import build_suggest_scenarios_prompt
        prompt = build_suggest_scenarios_prompt(page)
        cache_key = ("suggest_scenarios", page.get("url", ""), page.get("title", ""))
        raw = self.generate_json(prompt, timeout=180.0, cache_key=cache_key)
        if not raw or not isinstance(raw.get("scenarios"), list):
            return []
        out = []
        for s in raw["scenarios"]:
            if not isinstance(s, dict):
                continue
            name = s.get("name")
            ctx = s.get("ai_context")
            why = s.get("rationale", "")
            if not (isinstance(name, str) and name and
                    isinstance(ctx, str) and ctx):
                continue
            out.append({"name": name, "ai_context": ctx,
                        "rationale": why if isinstance(why, str) else ""})
        return out


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
