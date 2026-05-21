import os
import time
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


import time as _time
from unittest.mock import patch as _patch11


def test_generate_json_times_out(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = _time.monotonic()

    def slow_generate(**kwargs):
        _time.sleep(2.0)
        return {"response": '{"value": "late"}'}

    with _patch11.object(svc.client, "generate", side_effect=slow_generate):
        result = svc.generate_json("prompt", timeout=0.2)
    assert result is None
    assert "timeout" in (svc.last_error or "").lower()


def test_cache_hit_skips_second_call(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": '{"value": "x"}'}
        a = svc.generate_json("same prompt", cache_key=("match", "k1"))
        b = svc.generate_json("same prompt", cache_key=("match", "k1"))
    assert a == b == {"value": "x"}
    assert mock_gen.call_count == 1


def test_cache_invalidated_on_reload(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": '{"value": "a"}'}
        first = svc.generate_json("p", cache_key=("k",))
    assert first == {"value": "a"}

    # Reload should clear the cache. After clearing, a fresh call hits the model.
    # We exercise the cache-cleared invariant by checking _cache is empty.
    svc.reload()
    assert svc._cache == {}

    # And the cache write-back still works after reload — call once with a fresh
    # patched client and verify it caches.
    svc._available = True
    svc._available_at = time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": '{"value": "b"}'}
        a = svc.generate_json("p", cache_key=("k",))
        b = svc.generate_json("p", cache_key=("k",))
    assert a == b == {"value": "b"}
    assert mock_gen.call_count == 1


def test_cache_skipped_when_key_is_none(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": '{"value": "x"}'}
        svc.generate_json("same", cache_key=None)
        svc.generate_json("same", cache_key=None)
    assert mock_gen.call_count == 2


class _PydanticLikeResponse:
    """Stand-in for ollama._types.GenerateResponse: has `.response`, NOT a dict subclass."""
    def __init__(self, response: str):
        self.response = response


def test_generate_json_handles_pydantic_response_object(tmp_path):
    """Modern ollama SDK returns a GenerateResponse Pydantic object, not a dict.
    The previous implementation silently produced raw='' for these and returned
    None with no error — every AI feature broke without explanation. This test
    pins the fix.
    """
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = _PydanticLikeResponse('{"value": "ok"}')
        result = svc.generate_json("prompt")
    assert result == {"value": "ok"}
    assert svc.last_error is None


def test_generate_json_sets_last_error_on_unparseable(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": "not json"}
        result = svc.generate_json("prompt")
    assert result is None
    assert "unparseable" in (svc.last_error or "").lower()
    assert "not json" in (svc.last_error or "")


def test_generate_json_sets_last_error_on_empty_response(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc._available = True
    svc._available_at = time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = _PydanticLikeResponse("")
        result = svc.generate_json("prompt")
    assert result is None
    assert "empty response" in (svc.last_error or "").lower()


class _ListResponseLike:
    def __init__(self, names: list[str]):
        self.models = [type("M", (), {"model": n})() for n in names]


def test_is_available_reports_when_configured_model_missing(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc.model = "phi4:14b"
    with patch.object(svc.client, "list", return_value=_ListResponseLike(["mistral:latest"])):
        assert svc.is_available() is False
    assert "phi4:14b" in (svc.last_error or "")
    assert "not pulled" in (svc.last_error or "").lower()


def test_is_available_true_when_configured_model_present(tmp_path):
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc.model = "phi4:14b"
    with patch.object(svc.client, "list", return_value=_ListResponseLike(["phi4:14b", "mistral:latest"])):
        assert svc.is_available() is True
    assert svc.last_error is None


def test_is_available_true_when_listing_is_empty(tmp_path):
    """Older Ollama or pre-pull state may return empty list — don't false-negative."""
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc.model = "phi4:14b"
    with patch.object(svc.client, "list", return_value=_ListResponseLike([])):
        assert svc.is_available() is True
