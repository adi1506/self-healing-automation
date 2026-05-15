import json
import pytest
from pathlib import Path
from core.auth_session import (
    resolve_fernet_key, encrypt_storage_state, decrypt_storage_state,
    MissingStorageStateKey,
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
