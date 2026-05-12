# Unified AI Service & Phi-4 Rollout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-module Ollama clients with a single `AIService`, default to Phi-4 14B, expose a working model selector in Settings, and ship five new AI features on the same foundation.

**Architecture:** A single `core/ai_service.py` module owns the Ollama client, settings, JSON primitive, response cache, and shared thread pool. `core/ai_matcher.py` and `core/ai_test_data.py` become thin adapters over it so existing callers (`core/healer.py`, `core/test_case_generator.py`) keep their contracts. New features (Scenario Suggester, Healing Rationale, Failure Summarizer, Refine Row, Append Rows) are methods on the same service.

**Tech Stack:** Python 3.11+, Streamlit, Ollama Python SDK, PyYAML, pytest, `concurrent.futures.ThreadPoolExecutor`.

**Spec:** `docs/superpowers/specs/2026-05-12-unified-ai-service-and-phi4-rollout-design.md`

**Note on `git add -f`:** the repo's `.gitignore` excludes `docs/`. All commits that touch the spec or this plan must use `git add -f` for those paths. Source/test commits are unaffected.

---

## Phase 1 — Foundation: `AIService` + adapters (no behavior change)

Existing tests in `tests/test_ai_matcher.py`, `tests/test_ai_test_data.py`, `tests/test_ai_matcher_recipe.py` must stay green after this phase. No new features yet.

### Task 1: Extract prompt builders into `core/ai_prompts.py`

**Files:**
- Create: `core/ai_prompts.py`
- Create: `tests/test_ai_prompts.py`

- [ ] **Step 1: Write the failing test**

`tests/test_ai_prompts.py`:

```python
from core.ai_prompts import (
    build_match_prompt,
    build_recipe_prompt,
    build_field_value_prompt,
    PROMPT_VERSION,
)


def test_prompt_version_is_string():
    assert isinstance(PROMPT_VERSION, str)
    assert len(PROMPT_VERSION) > 0


def test_match_prompt_lists_candidates_by_index():
    old = {"element_name": "First Name", "element_type": "input-text",
           "placeholder": "", "locator_label": "First Name"}
    candidates = [
        {"element_name": "Email", "element_type": "input-email",
         "placeholder": "", "locator_label": "Email"},
        {"element_name": "Given Name", "element_type": "input-text",
         "placeholder": "", "locator_label": "Given Name"},
    ]
    prompt = build_match_prompt(old, candidates)
    assert "First Name" in prompt
    assert "Index 0" in prompt and "Index 1" in prompt
    assert "Given Name" in prompt
    assert "match_index" in prompt


def test_recipe_prompt_includes_goal_and_url():
    prompt = build_recipe_prompt(
        "https://example.com/signup",
        [{"element_name": "Email", "element_type": "input-email",
          "placeholder": "", "locator_label": ""}],
        "register a new user",
    )
    assert "register a new user" in prompt
    assert "https://example.com/signup" in prompt
    assert "Index 0" in prompt


def test_field_value_prompt_includes_constraints():
    field = {
        "element_name": "PAN", "element_type": "input-text",
        "locator_label": "PAN", "locator_name": "pan",
        "pattern": "[A-Z]{5}[0-9]{4}[A-Z]", "maxlength": "10",
        "required": True,
    }
    prompt = build_field_value_prompt(
        field, {"title": "KYC", "h1": "Identity", "first_paragraph": ""},
        per_field_rule="Use a valid PAN", ai_context="Indian resident",
    )
    assert "PAN" in prompt
    assert "[A-Z]{5}[0-9]{4}[A-Z]" in prompt
    assert "maxlength=10" in prompt
    assert "Indian resident" in prompt
    assert "Use a valid PAN" in prompt
    assert "\"value\"" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_ai_prompts.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'core.ai_prompts'`.

- [ ] **Step 3: Write minimal implementation**

`core/ai_prompts.py`:

```python
"""Prompt builders for AIService. Pure functions, no I/O, easily testable.

PROMPT_VERSION participates in the response cache key — bump on any template
change to invalidate stale entries.
"""
from __future__ import annotations

PROMPT_VERSION = "1"


def build_match_prompt(old_element: dict, candidates: list[dict]) -> str:
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


def build_recipe_prompt(page_url: str, elements: list[dict], goal: str) -> str:
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


def _summarize_constraints(field: dict) -> str:
    parts = []
    if field.get("pattern"): parts.append(f"pattern={field['pattern']}")
    if field.get("maxlength"): parts.append(f"maxlength={field['maxlength']}")
    if field.get("minlength"): parts.append(f"minlength={field['minlength']}")
    if field.get("min") not in ("", None): parts.append(f"min={field['min']}")
    if field.get("max") not in ("", None): parts.append(f"max={field['max']}")
    if field.get("required"): parts.append("required")
    if field.get("autocomplete"): parts.append(f"autocomplete={field['autocomplete']}")
    return ", ".join(parts)


def build_field_value_prompt(
    field: dict, page_context: dict, per_field_rule: str, ai_context: str,
) -> str:
    constraints = _summarize_constraints(field)
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
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_ai_prompts.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```
git add core/ai_prompts.py tests/test_ai_prompts.py
git commit -m "feat(core): extract prompt builders into ai_prompts module"
```

---

### Task 2: Create `core/ai_service.py` shell — connection, model resolution, `is_available`

**Files:**
- Create: `core/ai_service.py`
- Create: `tests/test_ai_service.py`

- [ ] **Step 1: Write the failing test**

`tests/test_ai_service.py`:

```python
import os
from unittest.mock import patch, MagicMock
import pytest
from core.ai_service import AIService, get_ai_service, reset_ai_service


@pytest.fixture(autouse=True)
def reset_singleton():
    reset_ai_service()
    yield
    reset_ai_service()


def test_defaults_to_phi4_when_no_env_or_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    svc = AIService(settings_path=str(tmp_path / "settings.yaml"))
    assert svc.model == "phi4:14b"
    assert svc.host == "http://localhost:11434"


def test_env_vars_override_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://other:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "granite4:8b")
    svc = AIService(settings_path=str(tmp_path / "settings.yaml"))
    assert svc.model == "granite4:8b"
    assert svc.host == "http://other:11434"


def test_settings_yaml_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "granite4:8b")
    settings = tmp_path / "settings.yaml"
    settings.write_text("ai:\n  host: http://saved:11434\n  model: qwen3:14b\n")
    svc = AIService(settings_path=str(settings))
    assert svc.model == "qwen3:14b"
    assert svc.host == "http://saved:11434"


def test_is_available_caches_result(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "settings.yaml"))
    with patch.object(svc.client, "list", return_value={"models": []}) as mock_list:
        assert svc.is_available() is True
        assert svc.is_available() is True  # cached
        assert mock_list.call_count == 1


def test_is_available_false_on_connection_error(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "settings.yaml"))
    with patch.object(svc.client, "list", side_effect=Exception("refused")):
        assert svc.is_available() is False
    assert "refused" in (svc.last_error or "")


def test_get_ai_service_is_singleton(tmp_path):
    a = get_ai_service(settings_path=str(tmp_path / "s.yaml"))
    b = get_ai_service(settings_path=str(tmp_path / "s.yaml"))
    assert a is b
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_ai_service.py -v
```

Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`core/ai_service.py`:

```python
"""Single owner of all Ollama interactions.

- Resolves host/model from data/settings.yaml > env vars > defaults.
- Caches is_available() for 30s.
- Exposes a singleton via get_ai_service().
"""
from __future__ import annotations

import os
import time
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
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_ai_service.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```
git add core/ai_service.py tests/test_ai_service.py
git commit -m "feat(core): AIService scaffolding with settings.yaml + env resolution"
```

---

### Task 3: Add `generate_json` primitive (no timeout/cache yet — Phase 4 adds those)

**Files:**
- Modify: `core/ai_service.py`
- Modify: `tests/test_ai_service.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_ai_service.py`)

```python
from unittest.mock import patch


def test_generate_json_parses_response(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = time.monotonic()  # bypass freshness check
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": '{"value": "alice@gmail.com"}'}
        result = svc.generate_json("prompt")
        assert result == {"value": "alice@gmail.com"}


def test_generate_json_strips_think_tags(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {
            "response": '<think>let me consider</think>\n{"value": "x"}'
        }
        result = svc.generate_json("prompt")
        assert result == {"value": "x"}


def test_generate_json_strips_code_fences(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": '```json\n{"value": "y"}\n```'}
        result = svc.generate_json("prompt")
        assert result == {"value": "y"}


def test_generate_json_returns_none_on_unavailable(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc.client = None
    assert svc.generate_json("prompt") is None


def test_generate_json_returns_none_on_invalid_json(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": "not json at all"}
        assert svc.generate_json("prompt") is None
```

Add `import time` at the top of the test file if not already present.

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_ai_service.py -v -k generate_json
```

Expected: 5 FAIL — `AttributeError: AIService has no attribute generate_json`.

- [ ] **Step 3: Implement `generate_json` in `core/ai_service.py`**

Add at the bottom of the `AIService` class:

```python
    # -------------------------------------------------------------- primitive
    def generate_json(self, prompt: str, *, timeout: float = 30.0,
                      cache_key: tuple | None = None) -> dict | None:
        """Generate a JSON response from the model.

        - Sets format=json and temperature=0.0.
        - Strips <think>...</think> and ```json fences before parsing.
        - Returns None on unavailable / timeout / invalid JSON.
        - timeout and cache_key are no-ops in Phase 1; wired in Phase 4.
        """
        if self.client is None or not self.is_available():
            return None
        start = time.monotonic()
        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                format="json",
                options={"temperature": 0.0},
            )
        except Exception as e:
            self.last_error = f"generate failed: {e}"
            return None
        finally:
            self.last_latency_ms = (time.monotonic() - start) * 1000.0

        raw = response.get("response", "") if isinstance(response, dict) else ""
        return self._parse_json_response(raw)

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
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_ai_service.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```
git add core/ai_service.py tests/test_ai_service.py
git commit -m "feat(core): AIService.generate_json primitive with think/fence stripping"
```

---

### Task 4: Move element-matching prompts onto `AIService` + refactor `AIMatcher` as adapter

**Files:**
- Modify: `core/ai_service.py`
- Modify: `core/ai_matcher.py`
- Modify: `tests/test_ai_matcher.py` (only to point at the adapter behavior — public API unchanged)

- [ ] **Step 1: Add high-level methods to `AIService`** (append inside the class)

```python
    # ----------------------------------------------------------- high level
    def match_element(self, old_element: dict, candidates: list[dict]) -> dict | None:
        from core.ai_prompts import build_match_prompt
        prompt = build_match_prompt(old_element, candidates)
        return self.generate_json(prompt, timeout=15.0)

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
```

- [ ] **Step 2: Replace `core/ai_matcher.py` with the adapter**

```python
"""Backwards-compatible adapter — delegates to core.ai_service.AIService.

Public API preserved so core/healer.py and tests don't break.
"""
from __future__ import annotations

from core.ai_service import get_ai_service


class AIMatcher:
    def __init__(self, host: str = "", model: str = ""):
        # host/model args ignored — AIService owns config now. Kept for
        # backwards compatibility with existing callers.
        self._svc = get_ai_service()
        # Test fixtures patch matcher.client.generate / matcher.client.list,
        # so expose the underlying client.
        self.client = self._svc.client
        self.host = self._svc.host
        self.model = self._svc.model

    # ------ availability cache shim (preserve _available attr for tests) ----
    @property
    def _available(self):
        return self._svc._available

    @_available.setter
    def _available(self, value):
        self._svc._available = value
        if value is not None:
            import time
            self._svc._available_at = time.monotonic()

    def is_available(self) -> bool:
        return self._svc.is_available()

    def match_element(self, old_element: dict, candidates: list[dict]) -> dict | None:
        return self._svc.match_element(old_element, candidates)

    def suggest_recipe(self, page_url: str, elements: list[dict],
                       goal: str) -> dict | None:
        return self._svc.suggest_recipe(page_url, elements, goal)
```

- [ ] **Step 3: Update `tests/test_ai_matcher.py` fixture so the singleton sees the patched client**

The existing tests patch `matcher.client.generate` / `matcher.client.list`. After the refactor, `matcher.client` is the same object as `_svc.client`, so the existing patches still work. But the singleton is shared across tests — add an autouse reset fixture.

At the top of `tests/test_ai_matcher.py`, after the imports, insert:

```python
from core.ai_service import reset_ai_service

@pytest.fixture(autouse=True)
def _reset_ai_singleton():
    reset_ai_service()
    yield
    reset_ai_service()
```

- [ ] **Step 4: Run the existing matcher tests**

```
pytest tests/test_ai_matcher.py tests/test_ai_matcher_recipe.py -v
```

Expected: all existing tests pass unchanged (the public behavior is preserved).

- [ ] **Step 5: Commit**

```
git add core/ai_service.py core/ai_matcher.py tests/test_ai_matcher.py
git commit -m "refactor(core): AIMatcher becomes adapter over AIService"
```

---

### Task 5: Move field-value generation onto `AIService` + refactor `AITestData` as adapter

**Files:**
- Modify: `core/ai_service.py`
- Modify: `core/ai_test_data.py`
- Modify: `tests/test_ai_test_data.py`

- [ ] **Step 1: Add `generate_field_value` to `AIService`** (append inside class)

```python
    def generate_field_value(
        self, field: dict, page_context: dict,
        per_field_rule: str = "", ai_context: str = "",
    ) -> str | None:
        """Generate one value for a single form field with one retry on
        constraint violation. Constraint validation is performed by the
        caller (AITestData adapter) — this method only handles the LLM call.

        Returns the raw value string or None.
        """
        from core.ai_prompts import build_field_value_prompt
        prompt = build_field_value_prompt(field, page_context, per_field_rule, ai_context)
        raw = self.generate_json(prompt, timeout=15.0)
        if not raw:
            return None
        val = raw.get("value")
        return val if isinstance(val, str) else None
```

- [ ] **Step 2: Replace `core/ai_test_data.py` with an adapter**

```python
"""Backwards-compatible adapter — delegates LLM calls to AIService.

The DOM-constraint validation logic stays here because it's not AI logic —
the AIService just produces values; we (AITestData) decide whether the value
is acceptable for the field.
"""
from __future__ import annotations

import re
from core.ai_service import get_ai_service


class AITestData:
    def __init__(self, host: str = "", model: str = ""):
        self._svc = get_ai_service()
        self.client = self._svc.client
        self.host = self._svc.host
        self.model = self._svc.model

    @property
    def _available(self):
        return self._svc._available

    @_available.setter
    def _available(self, value):
        self._svc._available = value
        if value is not None:
            import time
            self._svc._available_at = time.monotonic()

    def is_available(self) -> bool:
        return self._svc.is_available()

    def generate_value(
        self, field: dict, page_context: dict,
        per_field_rule: str = "", ai_context: str = "",
    ) -> str | None:
        value = self._svc.generate_field_value(field, page_context, per_field_rule, ai_context)
        if value is None:
            return None
        violation = self._validate_against_constraints(value, field)
        if not violation:
            return value
        # Retry once with violation feedback
        from core.ai_prompts import build_field_value_prompt
        prompt = build_field_value_prompt(field, page_context, per_field_rule, ai_context)
        retry_prompt = (
            prompt
            + f"\n\nYour previous answer violated: {violation}. Try again. "
              f"Return strict JSON only."
        )
        raw = self._svc.generate_json(retry_prompt, timeout=15.0)
        if not raw:
            return None
        value = raw.get("value")
        if not isinstance(value, str):
            return None
        if self._validate_against_constraints(value, field):
            return None
        return value

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
```

- [ ] **Step 3: Add autouse reset fixture to `tests/test_ai_test_data.py`**

After the imports:

```python
from core.ai_service import reset_ai_service

@pytest.fixture(autouse=True)
def _reset_ai_singleton():
    reset_ai_service()
    yield
    reset_ai_service()
```

- [ ] **Step 4: Run existing ai_test_data tests**

```
pytest tests/test_ai_test_data.py -v
```

Expected: all 5 existing tests pass.

- [ ] **Step 5: Commit**

```
git add core/ai_service.py core/ai_test_data.py tests/test_ai_test_data.py
git commit -m "refactor(core): AITestData becomes adapter over AIService"
```

---

### Task 6: Drop the `mistral` default in `core/healer.py`

**Files:**
- Modify: `core/healer.py:10-12`

- [ ] **Step 1: Edit the constructor**

Replace lines 10–12 of `core/healer.py`:

```python
    def __init__(self, ai_host: str = "", ai_model: str = ""):
        self.scanner = Scanner()
        self.ai_matcher = AIMatcher(host=ai_host, model=ai_model)
```

(Removes the `"mistral"` default — AIService owns the default now.)

- [ ] **Step 2: Run healer tests**

```
pytest tests/test_healer.py -v
```

Expected: all pass.

- [ ] **Step 3: Commit**

```
git add core/healer.py
git commit -m "refactor(core): drop hardcoded mistral default in Healer"
```

---

### Task 7: Full test sweep — Phase 1 acceptance gate

- [ ] **Step 1: Run the whole test suite**

```
pytest tests/ -v
```

Expected: every test that was passing before Phase 1 still passes. No regressions.

- [ ] **Step 2: If any test fails, fix the adapter — do not change the public AIMatcher/AITestData contract.**

The expected failure mode is patches that referenced internals removed during refactor. Update the patch target, not the contract.

- [ ] **Step 3: Commit any fixes**

```
git commit -am "fix(tests): adjust patches for AIService adapter refactor"
```

---

## Phase 2 — Settings page model selector

### Task 8: Settings UX — host editor, model radio, save

**Files:**
- Modify: `pages/5_settings.py` (full rewrite)

- [ ] **Step 1: Replace `pages/5_settings.py`**

```python
import streamlit as st
from core.ai_service import get_ai_service

st.set_page_config(page_title="Settings", layout="wide")
st.title("Settings")

svc = get_ai_service()

st.subheader("AI Model")

# Connection status row
col_status, col_test = st.columns([3, 1])
with col_status:
    if svc.is_available():
        st.success(f"Connected to {svc.host}")
    else:
        st.error(f"Not reachable at {svc.host}")
        if svc.last_error:
            st.caption(f"Last error: {svc.last_error}")
with col_test:
    if st.button("Test connection"):
        svc.reload()
        st.rerun()

# Host editor
new_host = st.text_input("Ollama host", value=svc.host)

# Installed models list
installed: list[str] = []
if svc.is_available():
    try:
        listing = svc.client.list()
        installed = [m.get("name") or m.get("model") for m in listing.get("models", [])]
        installed = [n for n in installed if n]
    except Exception as e:
        st.warning(f"Could not list models: {e}")

if installed:
    # Put current model first if present
    if svc.model in installed:
        ordered = [svc.model] + [m for m in installed if m != svc.model]
    else:
        ordered = installed
    selected = st.radio("Installed models", options=ordered,
                        index=0, key="model_selector")
else:
    st.info("No installed models found.")
    selected = svc.model

# Hint when recommended default missing
if installed and "phi4:14b" not in installed:
    st.warning("Recommended model `phi4:14b` is not installed. Run on the Ollama host:\n\n"
               "```\nollama pull phi4:14b\n```")

if st.button("Save selection"):
    svc.save_config(host=new_host, model=selected)
    st.success(f"Saved. Now using {selected} at {new_host}.")
    st.rerun()

st.subheader("Storage paths (read-only)")
st.code("data/scans/         — scanned pages + element maps\n"
        "data/scenarios/     — scenarios YAML\n"
        "data/recipes/       — legacy recipes (auto-migrated)\n"
        "data/flows/         — legacy flows (auto-migrated)\n"
        "screenshots/        — run screenshots\n"
        "data/settings.yaml  — AI host/model (this page writes here)",
        language="text")

st.subheader("Re-run migration")
if st.button("Migrate legacy data now"):
    from core.scenario_migration import migrate_all
    report = migrate_all(
        recipes_dir="data/recipes",
        flows_dir="data/flows",
        scans_dir="data/scans",
        scenarios_dir="data/scenarios",
    )
    st.success(f"Migration ran: {report}")
```

- [ ] **Step 2: Smoke-launch Streamlit**

```
streamlit run app.py
```

Navigate to Settings. Verify the page renders without exceptions when Ollama is **not** running (should show "Not reachable" status, no installed models). Stop the server.

- [ ] **Step 3: Commit**

```
git add pages/5_settings.py
git commit -m "feat(ui): plug-and-play model selector on Settings page"
```

---

## Phase 3 — Healing Rationale surface

### Task 9: Healer captures rationale + confidence into change records

**Files:**
- Modify: `core/healer.py` (Phase 2b and Phase 3 AI branches)
- Modify: `tests/test_healer.py` (add coverage for new fields)

- [ ] **Step 1: Write the failing test** (append to `tests/test_healer.py`)

```python
from unittest.mock import patch, MagicMock
from core.healer import Healer
from core.ai_service import reset_ai_service


def test_heal_records_ai_rationale_in_changes():
    reset_ai_service()
    h = Healer()
    h.scanner = MagicMock()
    h.scanner.scan.return_value = [
        {"sno": 1, "element_name": "Given Name", "element_type": "input-text",
         "locator_id": "given_name", "locator_name": "given_name",
         "locator_css": "", "locator_xpath": "", "locator_data_testid": "",
         "locator_label": "Given Name", "placeholder": "", "available_options": ""},
    ]
    em = MagicMock()
    em.read_element_map.return_value = [
        {"sno": 1, "element_name": "First Name", "element_type": "input-text",
         "locator_id": "first_name", "locator_name": "first_name",
         "locator_css": "", "locator_xpath": "", "locator_data_testid": "",
         "locator_label": "First Name", "placeholder": "", "available_options": ""},
    ]
    with patch.object(h.ai_matcher, "is_available", return_value=True), \
         patch.object(h.ai_matcher, "match_element",
                      return_value={"match_index": 0, "confidence": 0.93,
                                    "reasoning": "Both fields request a given name."}):
        report = h.heal("http://example.com", em)

    ai_change = next(c for c in report["changes"] if "Level 3" in c["healed_by"])
    assert ai_change["rationale"] == "Both fields request a given name."
    assert ai_change["confidence"] == 0.93
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_healer.py::test_heal_records_ai_rationale_in_changes -v
```

Expected: FAIL — `KeyError: 'rationale'`.

- [ ] **Step 3: Update `core/healer.py`** — capture rationale and propagate it.

In `core/healer.py`, modify the Phase 2b block (around lines 67–80):

```python
            # Phase 2b: Gray-zone candidates (0.5–0.75) — try Level 3 AI
            for score, s_idx, c_idx in pair_scores:
                if s_idx in assigned_s or c_idx in assigned_c:
                    continue
                if score >= 0.5:
                    if self.ai_matcher.is_available():
                        unmatched = [current_elements[c_idx]]
                        ai_result = self.ai_matcher.match_element(stored_elements[s_idx], unmatched)
                        if ai_result and ai_result.get("match_index") == 0 and ai_result.get("confidence", 0) >= 0.7:
                            results[s_idx] = {
                                "status": "CHANGED",
                                "current_index": c_idx,
                                "healed_by": "Level 3 (Ollama confirmed)",
                                "ai_rationale": ai_result.get("reasoning", ""),
                                "ai_confidence": ai_result.get("confidence", 0.0),
                            }
                            matched_stored.add(s_idx)
                            matched_current.add(c_idx)
                            assigned_s.add(s_idx)
                            assigned_c.add(c_idx)
```

And the Phase 3 block (around lines 86–97):

```python
        if unmatched_s and unmatched_c and self.ai_matcher.is_available():
            for s_idx in list(unmatched_s):
                unmatched_current = [current_elements[i] for i in unmatched_c]
                ai_result = self.ai_matcher.match_element(stored_elements[s_idx], unmatched_current)
                if ai_result and ai_result.get("match_index", -1) >= 0 and ai_result.get("confidence", 0) >= 0.7:
                    c_idx = unmatched_c[ai_result["match_index"]]
                    results[s_idx] = {
                        "status": "CHANGED",
                        "current_index": c_idx,
                        "healed_by": f"Level 3 (Ollama, {ai_result['confidence']:.0%})",
                        "ai_rationale": ai_result.get("reasoning", ""),
                        "ai_confidence": ai_result.get("confidence", 0.0),
                    }
                    matched_stored.add(s_idx)
                    matched_current.add(c_idx)
                    unmatched_s.remove(s_idx)
                    unmatched_c.remove(c_idx)
```

In the change-record assembly section (around line 128 — `elif match_result["status"] == "CHANGED"`), include the new fields when present:

```python
            elif match_result["status"] == "CHANGED":
                current = current_elements[match_result["current_index"]]
                change_details = self._compute_change_details(stored, current)
                change_record = {
                    "element_name": current.get("element_name", stored["element_name"]),
                    "change_details": change_details,
                    "healed_by": match_result["healed_by"],
                }
                if "ai_rationale" in match_result:
                    change_record["rationale"] = match_result["ai_rationale"]
                if "ai_confidence" in match_result:
                    change_record["confidence"] = match_result["ai_confidence"]
                changes.append(change_record)
                healed_elements.append(self._merge_element(stored, current, "CHANGED", change_details, match_result["healed_by"]))
```

- [ ] **Step 4: Run the new test + existing healer tests**

```
pytest tests/test_healer.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```
git add core/healer.py tests/test_healer.py
git commit -m "feat(core): capture AI rationale and confidence on healed changes"
```

---

### Task 10: Reports → Healing tab renders the "Why" column

**Files:**
- Modify: `ui/reports/healing.py`

- [ ] **Step 1: Inspect current `ui/reports/healing.py`** (look up how it builds its DataFrame from healing-log records — column list lives there).

Find the column-list / DataFrame assembly block. Add a `"Why"` column derived from `record.get("rationale", "")`, displayed truncated to ~80 chars. Place it between *Matched element* and *Confidence* (or wherever follows the existing match column).

Concretely, locate the block that turns log records into rows for `st.dataframe` and insert:

```python
        row["Why"] = (rec.get("rationale") or "")[:80]
        if rec.get("confidence") is not None:
            row["Confidence"] = f"{float(rec['confidence']):.0%}"
        else:
            row["Confidence"] = "—"
```

Rows whose record lacks `rationale` (Level 1 / Level 2 matches) get an empty string, which renders blank in `st.dataframe`. That matches the spec ("show `—` for non-AI heals" — we use empty string; if the existing renderer prefers an explicit dash, use `"—"` instead).

- [ ] **Step 2: Smoke-launch and inspect**

```
streamlit run app.py
```

Open Reports → Healing log. Expected: page renders without exception. Existing healing rows (without rationale) show blank/dash in Why column. (No AI runs have happened yet to populate live data — this is purely a render check.) Stop the server.

- [ ] **Step 3: Commit**

```
git add ui/reports/healing.py
git commit -m "feat(ui): show AI 'Why' rationale column on Reports > Healing tab"
```

---

## Phase 4 — Operational guts: timeout (Z2) + cache (Z3)

### Task 11: Per-call timeout via `ThreadPoolExecutor`

**Files:**
- Modify: `core/ai_service.py`
- Modify: `tests/test_ai_service.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_ai_service.py`)

```python
import time as _time
from unittest.mock import patch

def test_generate_json_times_out(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = _time.monotonic()

    def slow_generate(**kwargs):
        _time.sleep(2.0)
        return {"response": '{"value": "late"}'}

    with patch.object(svc.client, "generate", side_effect=slow_generate):
        result = svc.generate_json("prompt", timeout=0.2)
    assert result is None
    assert "timeout" in (svc.last_error or "").lower()
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_ai_service.py::test_generate_json_times_out -v
```

Expected: FAIL — current code blocks the full 2s and returns the late result.

- [ ] **Step 3: Rewrite `generate_json` to use the shared executor**

At the top of `core/ai_service.py`, add:

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
```

Add an executor onto `AIService.__init__`:

```python
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ai-svc")
```

Replace the body of `generate_json`:

```python
    def generate_json(self, prompt: str, *, timeout: float = 30.0,
                      cache_key: tuple | None = None) -> dict | None:
        if self.client is None or not self.is_available():
            return None
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
        return self._parse_json_response(raw)
```

- [ ] **Step 4: Run all AI service tests**

```
pytest tests/test_ai_service.py -v
```

Expected: all 12 pass, including the new timeout test.

- [ ] **Step 5: Commit**

```
git add core/ai_service.py tests/test_ai_service.py
git commit -m "feat(core): per-call timeout + cancel on AIService.generate_json"
```

---

### Task 12: Response cache (Z3) keyed by `(prompt_hash, model, PROMPT_VERSION)`

**Files:**
- Modify: `core/ai_service.py`
- Modify: `tests/test_ai_service.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_ai_service.py`)

```python
def test_cache_hit_skips_second_call(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = _time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": '{"value": "x"}'}
        a = svc.generate_json("same prompt", cache_key=("match", "k1"))
        b = svc.generate_json("same prompt", cache_key=("match", "k1"))
    assert a == b == {"value": "x"}
    assert mock_gen.call_count == 1


def test_cache_invalidated_on_reload(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = _time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": '{"value": "a"}'}
        svc.generate_json("p", cache_key=("k",))
        svc.reload()
        svc._available = True
        svc._available_at = _time.monotonic()
        mock_gen.return_value = {"response": '{"value": "b"}'}
        result = svc.generate_json("p", cache_key=("k",))
    assert result == {"value": "b"}


def test_cache_skipped_when_key_is_none(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = _time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": '{"value": "x"}'}
        svc.generate_json("same", cache_key=None)
        svc.generate_json("same", cache_key=None)
    assert mock_gen.call_count == 2
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_ai_service.py -v -k cache
```

Expected: 3 FAIL.

- [ ] **Step 3: Implement the cache**

In `core/ai_service.py`, add a hashlib import and a cache dict on `__init__`:

```python
import hashlib
```

```python
        # Response cache: hashable_key -> parsed dict
        self._cache: dict[str, dict] = {}
```

In `reload()`, clear the cache:

```python
        self._cache.clear()
```

Modify `generate_json` to honor the cache. Insert this block immediately after the availability check at the top of the method:

```python
        from core.ai_prompts import PROMPT_VERSION
        composite_key: str | None = None
        if cache_key is not None:
            payload = repr((cache_key, self.model, PROMPT_VERSION))
            composite_key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            cached = self._cache.get(composite_key)
            if cached is not None:
                return cached
```

And immediately before the final `return self._parse_json_response(raw)`, write back the cache:

```python
        parsed = self._parse_json_response(raw)
        if parsed is not None and composite_key is not None:
            # Bound the cache at 512 entries (LRU eviction via dict insertion order)
            if len(self._cache) >= 512:
                # Drop oldest entry
                oldest = next(iter(self._cache))
                self._cache.pop(oldest, None)
            self._cache[composite_key] = parsed
        return parsed
```

Note: replace the prior `return self._parse_json_response(raw)` line with the block above.

- [ ] **Step 4: Run the AI service tests**

```
pytest tests/test_ai_service.py -v
```

Expected: all 15 pass.

- [ ] **Step 5: Commit**

```
git add core/ai_service.py tests/test_ai_service.py
git commit -m "feat(core): response cache on AIService.generate_json"
```

---

### Task 13: Wire cache keys into the high-level methods

**Files:**
- Modify: `core/ai_service.py`

- [ ] **Step 1: Update `match_element` and `generate_field_value` to pass `cache_key`**

In `core/ai_service.py`, modify `match_element`:

```python
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
```

Modify `generate_field_value`:

```python
    def generate_field_value(
        self, field: dict, page_context: dict,
        per_field_rule: str = "", ai_context: str = "",
    ) -> str | None:
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
```

Retry calls (in `AITestData`) pass `cache_key=None` already because they call `generate_json` directly without a key. Verify by reading `core/ai_test_data.py` — the retry uses `self._svc.generate_json(retry_prompt, timeout=15.0)` with no `cache_key`, so it correctly bypasses the cache.

- [ ] **Step 2: Run the whole suite**

```
pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 3: Commit**

```
git add core/ai_service.py
git commit -m "feat(core): wire cache keys into match_element and field-value paths"
```

---

## Phase 5 — Refine Row (Feature X)

### Task 14: `AIService.refine_row` with locked-field protection

**Files:**
- Modify: `core/ai_service.py`
- Modify: `core/ai_prompts.py`
- Create: `tests/test_refine_row.py`

- [ ] **Step 1: Add the prompt builder to `core/ai_prompts.py`**

```python
def build_refine_row_prompt(
    field_defs: list[dict], current_row: dict[str, str],
    refine_prompt: str, locked: list[str],
) -> str:
    field_lines = []
    for f in field_defs:
        name = f.get("element_name", "")
        if name in locked:
            field_lines.append(
                f"  - {name} (LOCKED, must not change): current='{current_row.get(name, '')}', "
                f"type={f.get('element_type', '')}"
            )
        else:
            field_lines.append(
                f"  - {name}: current='{current_row.get(name, '')}', "
                f"type={f.get('element_type', '')}"
            )
    listing = "\n".join(field_lines)
    return (
        "You are adjusting a single test-data row for a web form.\n"
        f"User instruction: {refine_prompt}\n"
        "Fields and current values:\n"
        f"{listing}\n"
        "Return strict JSON only. Output every field name as a key with its NEW value. "
        "Fields marked LOCKED MUST keep their current value exactly. "
        "Other fields should change only if the user instruction implies a change.\n"
        '{"values": {"<field_name>": "<value>"}}'
    )
```

- [ ] **Step 2: Write the failing tests**

`tests/test_refine_row.py`:

```python
from unittest.mock import patch
import pytest
from core.ai_service import AIService, reset_ai_service


@pytest.fixture(autouse=True)
def _reset():
    reset_ai_service()
    yield
    reset_ai_service()


@pytest.fixture
def svc(tmp_path):
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s._available = True
    import time
    s._available_at = time.monotonic()
    return s


def test_refine_row_updates_only_ai_eligible_fields(svc):
    field_defs = [
        {"element_name": "city", "element_type": "input-text"},
        {"element_name": "pincode", "element_type": "input-text",
         "pattern": r"\d{6}"},
        {"element_name": "email", "element_type": "input-email"},
    ]
    current = {"city": "Mumbai", "pincode": "400001", "email": "a@b.com"}

    fake_response = (
        '{"values": {"city": "Bangalore", "pincode": "999", '
        '"email": "priya@gmail.com"}}'
    )
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": fake_response}
        new_row = svc.refine_row(field_defs, current,
                                 "change to Bangalore with Gmail")

    # Locked field (pincode has a pattern) must be preserved.
    assert new_row["pincode"] == "400001"
    # AI-eligible fields are updated.
    assert new_row["city"] == "Bangalore"
    assert new_row["email"] == "priya@gmail.com"


def test_refine_row_returns_none_when_unavailable(tmp_path):
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s.client = None
    assert s.refine_row([{"element_name": "x"}], {"x": "y"}, "tweak") is None


def test_refine_row_returns_none_on_invalid_response(svc):
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": "not json"}
        result = svc.refine_row(
            [{"element_name": "x", "element_type": "input-text"}],
            {"x": "y"}, "tweak",
        )
    assert result is None
```

- [ ] **Step 3: Run to verify they fail**

```
pytest tests/test_refine_row.py -v
```

Expected: 3 FAIL — `AttributeError: AIService has no attribute refine_row`.

- [ ] **Step 4: Implement `refine_row` in `core/ai_service.py`** (append inside class)

```python
    def refine_row(
        self, field_defs: list[dict], current_row: dict[str, str],
        refine_prompt: str,
    ) -> dict[str, str] | None:
        """Return a new row that respects the user instruction while leaving
        DOM-constrained fields untouched.
        """
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
```

- [ ] **Step 5: Run the new tests**

```
pytest tests/test_refine_row.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```
git add core/ai_service.py core/ai_prompts.py tests/test_refine_row.py
git commit -m "feat(core): AIService.refine_row with locked-field protection"
```

---

### Task 15: Refine-row UI in Dataset tab

**Files:**
- Modify: `ui/scenarios/dataset_tab.py`

- [ ] **Step 1: Read the current row-action rendering** in `ui/scenarios/dataset_tab.py` to find where per-row buttons (e.g. existing 🔄 Regenerate this row) are drawn. Look for the loop that iterates over `sc.dataset` and renders row controls.

- [ ] **Step 2: Add the Refine icon next to the Regenerate icon**

Inside the per-row controls loop, alongside the existing Regenerate button, add:

```python
        if st.button("✏️", key=f"refine_{sc.id}_{row_idx}",
                     help="Refine this row with AI"):
            st.session_state[f"refine_open_{sc.id}_{row_idx}"] = True
```

Then immediately below the row, render the inline prompt + preview when open:

```python
        if st.session_state.get(f"refine_open_{sc.id}_{row_idx}"):
            with st.container(border=True):
                refine_text = st.text_input(
                    f"Refine row {row_idx+1}",
                    key=f"refine_text_{sc.id}_{row_idx}",
                    placeholder="e.g. change to a Bangalore customer with a Gmail address",
                )
                colp, cola, cold = st.columns([1, 1, 1])
                if colp.button("Preview", key=f"refine_preview_{sc.id}_{row_idx}"):
                    em = ExcelManager(data_dir=DATA_SCANS)
                    elements = em.read_element_map(sc.base_url)
                    from core.ai_service import get_ai_service
                    svc = get_ai_service()
                    proposed = svc.refine_row(elements, sc.dataset[row_idx], refine_text)
                    st.session_state[f"refine_proposed_{sc.id}_{row_idx}"] = proposed

                proposed = st.session_state.get(f"refine_proposed_{sc.id}_{row_idx}")
                if proposed:
                    current = sc.dataset[row_idx]
                    diff_rows = []
                    for k, new_v in proposed.items():
                        old_v = current.get(k, "")
                        if old_v != new_v:
                            diff_rows.append({"Field": k, "Current": old_v,
                                               "New": new_v})
                    if diff_rows:
                        st.dataframe(pd.DataFrame(diff_rows), hide_index=True,
                                     use_container_width=True)
                        if cola.button("Apply", key=f"refine_apply_{sc.id}_{row_idx}"):
                            new_dataset = list(sc.dataset)
                            new_dataset[row_idx] = proposed
                            on_save(new_dataset)
                            st.session_state.pop(f"refine_open_{sc.id}_{row_idx}", None)
                            st.session_state.pop(f"refine_proposed_{sc.id}_{row_idx}", None)
                            st.rerun()
                    else:
                        st.info("No changes proposed.")
                if cold.button("Cancel", key=f"refine_cancel_{sc.id}_{row_idx}"):
                    st.session_state.pop(f"refine_open_{sc.id}_{row_idx}", None)
                    st.session_state.pop(f"refine_proposed_{sc.id}_{row_idx}", None)
                    st.rerun()
```

When AI is unavailable, disable the icon:

```python
        from core.ai_service import get_ai_service
        ai_ok = get_ai_service().is_available()
        if st.button("✏️", key=f"refine_{sc.id}_{row_idx}", disabled=not ai_ok,
                     help=("Refine this row with AI" if ai_ok
                           else "Refine with AI requires Ollama — see Settings")):
            ...
```

- [ ] **Step 3: Smoke-launch**

```
streamlit run app.py
```

Open an existing scenario with a dataset. Confirm the ✏️ icon renders. Stop the server.

- [ ] **Step 4: Commit**

```
git add ui/scenarios/dataset_tab.py
git commit -m "feat(ui): inline Refine Row with diff preview on Dataset tab"
```

---

## Phase 6 — Append Rows (Feature Y) + parallelism (Z4)

### Task 16: `AIService.generate_complementary_rows` with parallel fan-out

**Files:**
- Modify: `core/ai_service.py`
- Modify: `core/ai_prompts.py`
- Create: `tests/test_append_rows.py`

- [ ] **Step 1: Add the prompt builder**

In `core/ai_prompts.py`:

```python
def build_complementary_row_prompt(
    field_defs: list[dict], existing_rows: list[dict],
    batch_context: str, row_position: int,
) -> str:
    field_names = [f.get("element_name", "") for f in field_defs]
    existing_summary = "\n".join(
        f"  Row {i+1}: " + ", ".join(f"{k}={v}" for k, v in r.items() if k in field_names)
        for i, r in enumerate(existing_rows)
    ) or "  (no existing rows)"
    return (
        "You are generating ONE complementary test-data row for a web form.\n"
        f"Batch context: {batch_context}\n"
        f"Fields: {', '.join(field_names)}\n"
        f"Existing rows in this dataset (do not duplicate):\n{existing_summary}\n"
        f"This is row #{row_position} of the new batch — make it distinct from "
        "both existing rows and the other rows in this batch.\n"
        "Return strict JSON only with every field as a key:\n"
        '{"values": {"<field_name>": "<value>"}}'
    )
```

- [ ] **Step 2: Write the failing tests**

`tests/test_append_rows.py`:

```python
from unittest.mock import patch
import pytest
from core.ai_service import AIService, reset_ai_service


@pytest.fixture(autouse=True)
def _reset():
    reset_ai_service()
    yield
    reset_ai_service()


@pytest.fixture
def svc(tmp_path):
    import time
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s._available = True
    s._available_at = time.monotonic()
    return s


def test_generate_complementary_rows_returns_n_rows(svc):
    field_defs = [
        {"element_name": "name", "element_type": "input-text"},
        {"element_name": "city", "element_type": "input-text"},
    ]
    responses = [
        {"response": '{"values": {"name": "A", "city": "Paris"}}'},
        {"response": '{"values": {"name": "B", "city": "Berlin"}}'},
        {"response": '{"values": {"name": "C", "city": "Madrid"}}'},
    ]
    with patch.object(svc.client, "generate", side_effect=responses):
        rows = svc.generate_complementary_rows(
            field_defs, existing_rows=[], batch_context="EU customers", n=3,
        )
    assert len(rows) == 3
    assert {r["city"] for r in rows} == {"Paris", "Berlin", "Madrid"}


def test_generate_complementary_rows_skips_invalid_responses(svc):
    field_defs = [{"element_name": "x", "element_type": "input-text"}]
    responses = [
        {"response": '{"values": {"x": "a"}}'},
        {"response": "garbage"},
        {"response": '{"values": {"x": "c"}}'},
    ]
    with patch.object(svc.client, "generate", side_effect=responses):
        rows = svc.generate_complementary_rows(field_defs, [], "ctx", n=3)
    # Two valid rows survive; the garbage one is dropped.
    assert len(rows) == 2
    assert {r["x"] for r in rows} == {"a", "c"}


def test_generate_complementary_rows_returns_empty_when_unavailable(tmp_path):
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s.client = None
    rows = s.generate_complementary_rows(
        [{"element_name": "x"}], [], "ctx", n=3,
    )
    assert rows == []
```

- [ ] **Step 3: Run to verify they fail**

```
pytest tests/test_append_rows.py -v
```

Expected: 3 FAIL — method not defined.

- [ ] **Step 4: Implement on `AIService`** (append inside class)

```python
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
            raw = self.generate_json(prompt, timeout=15.0)
            if not raw or not isinstance(raw.get("values"), dict):
                return None
            row: dict[str, str] = {}
            for f in field_defs:
                name = f.get("element_name", "")
                if not name:
                    continue
                v = raw["values"].get(name, "")
                row[name] = v if isinstance(v, str) else str(v)
            return row

        futures = [self._executor.submit(_one_row, i + 1) for i in range(n)]
        out: list[dict] = []
        for fut in futures:
            try:
                row = fut.result(timeout=20.0)
            except Exception:
                row = None
            if row is not None:
                out.append(row)
        return out
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_append_rows.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```
git add core/ai_service.py core/ai_prompts.py tests/test_append_rows.py
git commit -m "feat(core): AIService.generate_complementary_rows with parallel fan-out"
```

---

### Task 17: Append-rows UI in Dataset tab

**Files:**
- Modify: `ui/scenarios/dataset_tab.py`

- [ ] **Step 1: Add the UI block below the existing dataset grid**

Append, after the grid render:

```python
    from core.ai_service import get_ai_service
    svc = get_ai_service()
    ai_ok = svc.is_available()

    with st.container(border=True):
        st.markdown("**Add AI rows**")
        ca, cb, cc = st.columns([1, 4, 1])
        n_rows = ca.number_input("Count", min_value=1, max_value=8, value=3,
                                  key=f"add_n_{sc.id}", disabled=not ai_ok)
        batch_ctx = cb.text_input(
            "Context for the new rows", key=f"add_ctx_{sc.id}",
            placeholder="e.g. international customers from EU",
            disabled=not ai_ok,
        )
        if cc.button("Generate", key=f"add_btn_{sc.id}", disabled=not ai_ok):
            em = ExcelManager(data_dir=DATA_SCANS)
            elements = em.read_element_map(sc.base_url)
            with st.spinner(f"Generating {int(n_rows)} rows…"):
                new_rows = svc.generate_complementary_rows(
                    elements, list(sc.dataset), batch_ctx, int(n_rows),
                )
            if not new_rows:
                st.warning("No rows generated.")
            else:
                merged = list(sc.dataset) + [
                    {**r, "__expected_outcome": "success"} for r in new_rows
                ]
                on_save(merged)
                st.success(f"Added {len(new_rows)} rows.")
                st.rerun()
        if not ai_ok:
            st.caption("Requires Ollama — configure in Settings.")
```

- [ ] **Step 2: Smoke-launch**

```
streamlit run app.py
```

Open a scenario. Confirm the **Add AI rows** panel renders. Stop the server.

- [ ] **Step 3: Commit**

```
git add ui/scenarios/dataset_tab.py
git commit -m "feat(ui): Add AI rows with batch context (parallel generation)"
```

---

## Phase 7 — Failure Summarizer

### Task 18: `AIService.summarize_run` with per-run cache

**Files:**
- Modify: `core/ai_service.py`
- Modify: `core/ai_prompts.py`
- Create: `tests/test_failure_summarizer.py`

- [ ] **Step 1: Add the prompt builder**

In `core/ai_prompts.py`:

```python
def build_summarize_run_prompt(run_record: dict) -> str:
    name = run_record.get("scenario_name") or run_record.get("name") or "(unnamed)"
    steps = run_record.get("steps", [])
    step_lines = []
    for i, s in enumerate(steps, start=1):
        outcome = s.get("outcome", "?")
        err = s.get("error", "")
        action = s.get("action", "")
        target = s.get("target", "")
        line = f"  Step {i}: {action} {target} -> {outcome}"
        if err:
            line += f" — {err}"
        step_lines.append(line)
    heals = run_record.get("healings", [])
    heal_summary = (
        f"\nHealings during this run ({len(heals)}):\n"
        + "\n".join(
            f"  - {h.get('element_name', '?')}: {h.get('healed_by', '')}"
            for h in heals
        )
        if heals else ""
    )
    return (
        "Summarize this failed test run in one short paragraph (max ~80 words). "
        "State what failed, the likely root cause, and whether healings affected the outcome.\n"
        f"Scenario: {name}\n"
        "Steps:\n"
        + "\n".join(step_lines)
        + heal_summary
        + "\nReturn strict JSON: {\"summary\": \"<one paragraph>\"}"
    )
```

- [ ] **Step 2: Write the failing tests**

`tests/test_failure_summarizer.py`:

```python
from unittest.mock import patch
import pytest
from core.ai_service import AIService, reset_ai_service


@pytest.fixture(autouse=True)
def _reset():
    reset_ai_service()
    yield
    reset_ai_service()


@pytest.fixture
def svc(tmp_path):
    import time
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s._available = True
    s._available_at = time.monotonic()
    return s


def _run():
    return {
        "id": "run-42",
        "scenario_name": "Apply for FD",
        "steps": [
            {"action": "fill", "target": "name", "outcome": "ok"},
            {"action": "submit", "target": "form",
             "outcome": "fail", "error": "Email rejected: pattern mismatch"},
        ],
        "healings": [
            {"element_name": "email", "healed_by": "Level 2 (attribute, 82%)"},
        ],
    }


def test_summarize_run_returns_summary_string(svc):
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {
            "response": '{"summary": "Run failed at submit due to a stricter '
                        'email pattern. Two selectors healed."}'
        }
        text = svc.summarize_run(_run())
    assert text.startswith("Run failed")
    assert "email" in text.lower()


def test_summarize_run_caches_per_run_and_model(svc):
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": '{"summary": "..."}'}
        a = svc.summarize_run(_run())
        b = svc.summarize_run(_run())
    assert a == b
    assert mock_gen.call_count == 1


def test_summarize_run_returns_empty_when_unavailable(tmp_path):
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s.client = None
    assert s.summarize_run(_run()) == ""
```

- [ ] **Step 3: Run to verify they fail**

```
pytest tests/test_failure_summarizer.py -v
```

Expected: 3 FAIL.

- [ ] **Step 4: Implement on `AIService`** (append inside class)

```python
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
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_failure_summarizer.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```
git add core/ai_service.py core/ai_prompts.py tests/test_failure_summarizer.py
git commit -m "feat(core): AIService.summarize_run with per-run cache"
```

---

### Task 19: Render AI summary on Reports → Run detail

**Files:**
- Modify: `ui/reports/runs.py`

- [ ] **Step 1: Inspect `ui/reports/runs.py`** to find where individual run details are rendered (look for the function that displays a single run record — likely triggered by clicking a row).

- [ ] **Step 2: Add an AI summary callout** at the top of the run-detail render, but only for failed runs:

```python
def _render_ai_summary(run_record: dict) -> None:
    status = (run_record.get("status") or "").lower()
    if status not in ("fail", "failed", "error"):
        return
    from core.ai_service import get_ai_service
    svc = get_ai_service()
    if not svc.is_available():
        return
    with st.spinner("Summarizing failure…"):
        summary = svc.summarize_run(run_record)
    if summary:
        st.warning(f"**AI summary** — {summary}")
```

Call `_render_ai_summary(run_record)` at the start of the existing run-detail function.

- [ ] **Step 3: Smoke-launch**

```
streamlit run app.py
```

Open Reports → Run history. Click into any failed run. Confirm: with Ollama unavailable, the callout simply doesn't render and no exception fires.

- [ ] **Step 4: Commit**

```
git add ui/reports/runs.py
git commit -m "feat(ui): AI failure summary callout on Reports > Run detail"
```

---

## Phase 8 — Scenario Suggester

### Task 20: `AIService.suggest_scenarios`

**Files:**
- Modify: `core/ai_service.py`
- Modify: `core/ai_prompts.py`
- Create: `tests/test_scenario_suggester.py`

- [ ] **Step 1: Add the prompt builder**

In `core/ai_prompts.py`:

```python
def build_suggest_scenarios_prompt(page: dict) -> str:
    elements = page.get("elements", [])
    listing = "\n".join(
        f"  - {e.get('element_name', '')} ({e.get('element_type', '')})"
        for e in elements
    ) or "  (no elements)"
    title = page.get("title") or page.get("url") or "(untitled page)"
    return (
        "Propose 6 distinct test scenarios for the page below. Mix happy-path "
        "and edge-case personas. Each scenario gets a short name, an ai_context "
        "(plain-English persona/scenario sentence), and a one-line rationale.\n"
        f"Page: {title}\n"
        f"Fields:\n{listing}\n"
        'Return strict JSON: {"scenarios": [{"name": "...", "ai_context": "...", '
        '"rationale": "..."}, ...]}'
    )
```

- [ ] **Step 2: Write the failing tests**

`tests/test_scenario_suggester.py`:

```python
from unittest.mock import patch
import pytest
from core.ai_service import AIService, reset_ai_service


@pytest.fixture(autouse=True)
def _reset():
    reset_ai_service()
    yield
    reset_ai_service()


@pytest.fixture
def svc(tmp_path):
    import time
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s._available = True
    s._available_at = time.monotonic()
    return s


def _page():
    return {
        "url": "https://example.com/apply",
        "title": "Fixed Deposit Application",
        "elements": [
            {"element_name": "name", "element_type": "input-text"},
            {"element_name": "age", "element_type": "input-number"},
            {"element_name": "city", "element_type": "input-text"},
        ],
    }


def test_suggest_scenarios_returns_list_with_required_fields(svc):
    fake = (
        '{"scenarios": ['
        '{"name": "Senior FD", "ai_context": "Senior citizen from Mumbai",'
        ' "rationale": "Tests senior interest tier"},'
        '{"name": "Foreign address", "ai_context": "International applicant",'
        ' "rationale": "Tests country constraint"}'
        ']}'
    )
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": fake}
        suggestions = svc.suggest_scenarios(_page())
    assert len(suggestions) == 2
    for s in suggestions:
        assert s["name"] and s["ai_context"] and s["rationale"]


def test_suggest_scenarios_returns_empty_when_unavailable(tmp_path):
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s.client = None
    assert s.suggest_scenarios(_page()) == []


def test_suggest_scenarios_drops_malformed_entries(svc):
    fake = (
        '{"scenarios": ['
        '{"name": "ok", "ai_context": "ctx", "rationale": "why"},'
        '{"name": "missing_ctx"},'
        '{"ai_context": "no_name", "rationale": "x"}'
        ']}'
    )
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": fake}
        suggestions = svc.suggest_scenarios(_page())
    assert len(suggestions) == 1
    assert suggestions[0]["name"] == "ok"
```

- [ ] **Step 3: Run to verify they fail**

```
pytest tests/test_scenario_suggester.py -v
```

Expected: 3 FAIL.

- [ ] **Step 4: Implement on `AIService`** (append inside class)

```python
    def suggest_scenarios(self, page: dict) -> list[dict]:
        if self.client is None or not self.is_available():
            return []
        from core.ai_prompts import build_suggest_scenarios_prompt
        prompt = build_suggest_scenarios_prompt(page)
        cache_key = ("suggest_scenarios", page.get("url", ""), page.get("title", ""))
        raw = self.generate_json(prompt, timeout=30.0, cache_key=cache_key)
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
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_scenario_suggester.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```
git add core/ai_service.py core/ai_prompts.py tests/test_scenario_suggester.py
git commit -m "feat(core): AIService.suggest_scenarios from a scanned page"
```

---

### Task 21: Suggest-scenarios panel on Scenarios → New form

**Files:**
- Modify: `pages/3_scenarios.py`

- [ ] **Step 1: Inspect `pages/3_scenarios.py`** to find where the "New scenario" form is rendered (likely a button or expander that opens a form with name + base URL fields).

- [ ] **Step 2: Add the helper function** near the top of `pages/3_scenarios.py`, after `_unique_slug` (around line 30):

```python
def _create_scenario_from_suggestion(name: str, base_url: str, ai_context: str) -> str:
    """Mirror the manual New Scenario submit: create a Scenario with one
    initial dataset row carrying the suggested ai_context. Returns the new
    scenario id. Same store, same shape — no parallel code path.
    """
    sid = _unique_slug(_slugify(name))
    sc = Scenario(
        id=sid, name=name, kind="single-page", base_url=base_url,
        steps=[{"action": "fill", "target": "", "value": ""}],
        dataset=[{"__ai_context": ai_context, "__expected_outcome": "success"}],
        expected_outcome="success",
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    save_scenario(DATA_SCENARIOS, sc)
    return sid
```

- [ ] **Step 3: Add the Suggest panel** inside the `if open_id == "__new__":` branch, immediately after the existing `base_url = st.selectbox(...)` line (around line 39, before the "Create" button):

```python
    from core.ai_service import get_ai_service
    svc = get_ai_service()
    ai_ok = svc.is_available()

    with st.expander("✨ Suggest scenarios with AI", expanded=False):
        if not ai_ok:
            st.caption("Requires Ollama — configure in Settings.")
        elif not base_url:
            st.caption("Pick a scanned page above to enable suggestions.")
        else:
            if st.button("Suggest", key="suggest_btn"):
                elements = em.read_element_map(base_url)
                page_ctx = em.read_page_context(base_url) or {}
                page = {
                    "url": base_url,
                    "title": page_ctx.get("title", ""),
                    "elements": elements,
                }
                with st.spinner("Asking the model for scenario ideas…"):
                    st.session_state["scenario_suggestions"] = svc.suggest_scenarios(page)

            suggestions = st.session_state.get("scenario_suggestions") or []
            for i, s in enumerate(suggestions):
                with st.container(border=True):
                    st.markdown(f"**{s['name']}** — {s['rationale']}")
                    st.caption(f"AI Context: _{s['ai_context']}_")
                    if st.button("Add as scenario", key=f"add_sugg_{i}"):
                        sid = _create_scenario_from_suggestion(
                            name=s["name"], base_url=base_url,
                            ai_context=s["ai_context"],
                        )
                        st.session_state["_open_scenario"] = sid
                        st.session_state.pop("scenario_suggestions", None)
                        st.rerun()
```

- [ ] **Step 4: Smoke-launch**

```
streamlit run app.py
```

Open Scenarios → New. Expand **Suggest scenarios with AI**. Confirm the panel renders. With Ollama unavailable, confirm the caption shows the configure-in-Settings message. Stop the server.

- [ ] **Step 5: Commit**

```
git add pages/3_scenarios.py
git commit -m "feat(ui): Suggest scenarios with AI panel on Scenarios page"
```

---

## Phase 9 — README + final acceptance

### Task 22: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the "Ollama (Optional)" section**

Replace the section with:

```markdown
## AI Model

The app uses a local Ollama server. The default and recommended model is **Phi-4 14B**:

```bash
# Install Ollama from https://ollama.com, then:
ollama pull phi4:14b
ollama serve
```

Open the **Settings** page in the app to pick from any installed model. Selection is saved to `data/settings.yaml` and takes effect immediately — no restart.

Alternative models that work well on a CPU-only box:

- `granite4:8b` — fastest, JSON-native.
- `qwen3:14b` — strong multilingual coverage.
- `gemma4:12b` — agent-tuned.
- `mistral:7b` — legacy, smallest footprint.

The tool works fully without Ollama — AI matching is a last-resort heal fallback, and test-data generation falls back to heuristic layers.
```

- [ ] **Step 2: Commit**

```
git add README.md
git commit -m "docs: update README for Phi-4 default and in-app model selector"
```

---

### Task 23: Full-suite final acceptance

- [ ] **Step 1: Run the whole test suite**

```
pytest tests/ -v
```

Expected: every test passes. Count should be: previously-passing tests + new tests from this plan (`test_ai_prompts.py`, `test_ai_service.py`, `test_refine_row.py`, `test_append_rows.py`, `test_failure_summarizer.py`, `test_scenario_suggester.py`, new healer test).

- [ ] **Step 2: Smoke-launch end-to-end on EC2 (manual)**

1. `ollama pull phi4:14b` on the EC2.
2. `streamlit run app.py`.
3. Settings → confirm Phi-4 is selected; click Test connection (should be green).
4. Library → re-scan an existing form.
5. Scenarios → New → Suggest scenarios → Add one suggestion → run it.
6. Open Reports → Run detail on a failed run → confirm AI summary renders.
7. Reports → Healing log → confirm Why column populates on a run that exercised AI healing.
8. Dataset tab → Refine a row → verify diff preview and apply.
9. Dataset tab → Add 3 AI rows with a batch context → verify existing rows untouched.

- [ ] **Step 3: Final commit (if any smoke fixes were needed)**

```
git commit -am "fix: post-smoke adjustments"
```

---

## Self-Review

**Spec coverage:**

- Goal 1 (Phi-4 default): Task 2 sets DEFAULT_MODEL; Task 22 documents.
- Goal 2 (Plug-and-play selector): Task 8.
- Goal 3 (Unified AIService): Tasks 1–6.
- Goal 4 new features:
  - Scenario Suggester: Tasks 20, 21.
  - Healing Rationale: Tasks 9, 10.
  - Failure Summarizer: Tasks 18, 19.
- Goal 5 existing-feature enhancements:
  - Refine Row: Tasks 14, 15.
  - Append Rows: Tasks 16, 17.
- Goal 6 operational guts: Tasks 11 (timeout), 12 (cache), 16 (parallelism inside Append Rows).

**Placeholder scan:** no placeholders. All code blocks are complete. The Task 21 helper `_create_scenario_from_suggestion` is fully written and mirrors the existing manual handler in `pages/3_scenarios.py` (lines 41–50).

**Type consistency:**

- `match_element` returns the model's dict with keys `match_index`, `confidence`, `reasoning` — consistent across Tasks 4 and 9.
- `generate_complementary_rows` returns `list[dict[str, str]]` — consistent in Tasks 16 and 17.
- `refine_row` returns `dict[str, str] | None` — consistent in Tasks 14 and 15.
- `summarize_run` returns `str` ("" when unavailable) — consistent in Tasks 18 and 19.
- `suggest_scenarios` returns `list[dict]` with keys `name`, `ai_context`, `rationale` — consistent in Tasks 20 and 21.
- `AIService` singleton accessor is `get_ai_service()` everywhere; reset via `reset_ai_service()` in tests — consistent.
