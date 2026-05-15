from __future__ import annotations
import json
from pathlib import Path
import yaml
from cryptography.fernet import Fernet

DEFAULT_SETTINGS_PATH = "data/settings.yaml"


class MissingStorageStateKey(RuntimeError):
    """Raised when settings.yaml has no storage_state_key entry.

    The user must generate one and set it. Generate with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """


def resolve_fernet_key(settings_path: str = DEFAULT_SETTINGS_PATH) -> bytes:
    p = Path(settings_path)
    if not p.exists():
        raise MissingStorageStateKey(
            f"settings.yaml not found at {settings_path}; cannot resolve storage_state_key"
        )
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw = data.get("storage_state_key")
    if not raw:
        raise MissingStorageStateKey(
            "settings.yaml is missing storage_state_key. Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
            "and add it to data/settings.yaml as 'storage_state_key: <value>'."
        )
    return raw.encode() if isinstance(raw, str) else raw


def encrypt_storage_state(payload: dict, *, settings_path: str = DEFAULT_SETTINGS_PATH) -> bytes:
    key = resolve_fernet_key(settings_path)
    return Fernet(key).encrypt(json.dumps(payload).encode("utf-8"))


def decrypt_storage_state(blob: bytes, *, settings_path: str = DEFAULT_SETTINGS_PATH) -> dict:
    key = resolve_fernet_key(settings_path)
    return json.loads(Fernet(key).decrypt(blob).decode("utf-8"))
