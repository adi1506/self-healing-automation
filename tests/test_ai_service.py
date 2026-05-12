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
