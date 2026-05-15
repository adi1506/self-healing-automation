import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from core.applications import Application
from core.auth_session import (
    resolve_fernet_key, encrypt_storage_state, decrypt_storage_state,
    MissingStorageStateKey,
    save_storage_state, load_storage_state, is_storage_state_valid,
    delete_storage_state,
)


def test_resolve_key_from_settings_file(tmp_path):
    settings_path = tmp_path / "settings.yaml"
    # Fernet key is a urlsafe-base64-encoded 32-byte value
    settings_path.write_text("storage_state_key: 'YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU='\n", encoding="utf-8")
    key = resolve_fernet_key(str(settings_path))
    assert isinstance(key, bytes)
    assert len(key) == 44  # urlsafe-b64 of 32 bytes


def test_resolve_key_missing_raises(tmp_path):
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("ollama_host: 'http://localhost'\n", encoding="utf-8")
    with pytest.raises(MissingStorageStateKey):
        resolve_fernet_key(str(settings_path))


def test_encrypt_decrypt_round_trip(tmp_path):
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("storage_state_key: 'YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU='\n", encoding="utf-8")
    payload = {"cookies": [{"name": "session", "value": "abc"}], "origins": []}
    blob = encrypt_storage_state(payload, settings_path=str(settings_path))
    assert blob != json.dumps(payload).encode()  # actually encrypted
    out = decrypt_storage_state(blob, settings_path=str(settings_path))
    assert out == payload


@pytest.fixture
def settings_with_key(tmp_path):
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("storage_state_key: 'YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXowMTIzNDU='\n", encoding="utf-8")
    return str(settings_path)


def test_save_and_load_storage_state(tmp_path, settings_with_key):
    payload = {"cookies": [{"name": "PHPSESSID", "value": "xyz"}], "origins": []}
    save_storage_state(str(tmp_path), "app-1", payload, settings_path=settings_with_key)
    loaded = load_storage_state(str(tmp_path), "app-1", settings_path=settings_with_key)
    assert loaded == payload


def test_load_missing_returns_none(tmp_path, settings_with_key):
    assert load_storage_state(str(tmp_path), "nope", settings_path=settings_with_key) is None


def test_delete_storage_state(tmp_path, settings_with_key):
    save_storage_state(str(tmp_path), "app-1", {"cookies": []}, settings_path=settings_with_key)
    delete_storage_state(str(tmp_path), "app-1")
    assert load_storage_state(str(tmp_path), "app-1", settings_path=settings_with_key) is None


def test_is_storage_state_valid_uses_expiry_field():
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    fresh = Application(id="x", name="x", base_url_pattern="x", storage_state_expires_at=future, storage_state_path="x")
    expired = Application(id="x", name="x", base_url_pattern="x", storage_state_expires_at=past, storage_state_path="x")
    missing = Application(id="x", name="x", base_url_pattern="x")
    assert is_storage_state_valid(fresh) is True
    assert is_storage_state_valid(expired) is False
    assert is_storage_state_valid(missing) is False
