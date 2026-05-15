from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import yaml
from cryptography.fernet import Fernet

from core.applications import Application

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


def _state_path(data_dir: str, app_id: str) -> str:
    return os.path.join(data_dir, f"{app_id}.enc")


def save_storage_state(
    data_dir: str, app_id: str, payload: dict, *, settings_path: str = DEFAULT_SETTINGS_PATH
) -> str:
    os.makedirs(data_dir, exist_ok=True)
    blob = encrypt_storage_state(payload, settings_path=settings_path)
    path = _state_path(data_dir, app_id)
    with open(path, "wb") as f:
        f.write(blob)
    return path


def load_storage_state(
    data_dir: str, app_id: str, *, settings_path: str = DEFAULT_SETTINGS_PATH
) -> dict | None:
    path = _state_path(data_dir, app_id)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        blob = f.read()
    return decrypt_storage_state(blob, settings_path=settings_path)


def delete_storage_state(data_dir: str, app_id: str) -> None:
    path = _state_path(data_dir, app_id)
    if os.path.exists(path):
        os.remove(path)


def is_storage_state_valid(app: Application) -> bool:
    """Best-effort expiry check using the stored expiry timestamp.

    Returns False if expiry is missing, malformed, or in the past.
    A True result is only an upper bound — the server may have invalidated
    the session early. Replay handles that case by detecting 401/redirect
    to login and forcing a refresh.
    """
    if not app.storage_state_path or not app.storage_state_expires_at:
        return False
    try:
        expires = datetime.fromisoformat(app.storage_state_expires_at)
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > datetime.now(timezone.utc)
