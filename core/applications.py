from __future__ import annotations
import os
from dataclasses import dataclass, asdict
from typing import Optional
import yaml


@dataclass
class Application:
    id: str
    name: str
    base_url_pattern: str
    login_recording_id: Optional[str] = None
    storage_state_path: Optional[str] = None
    storage_state_captured_at: Optional[str] = None
    storage_state_expires_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _path(data_dir: str, app_id: str) -> str:
    return os.path.join(data_dir, f"{app_id}.yaml")


def save_application(data_dir: str, app: Application) -> None:
    os.makedirs(data_dir, exist_ok=True)
    with open(_path(data_dir, app.id), "w", encoding="utf-8") as f:
        yaml.safe_dump(app.to_dict(), f, sort_keys=False)


def load_application(data_dir: str, app_id: str) -> Application:
    with open(_path(data_dir, app_id), encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Application(**data)


def list_applications(data_dir: str) -> list[Application]:
    if not os.path.isdir(data_dir):
        return []
    out: list[Application] = []
    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".yaml") or fname.startswith("_"):
            continue
        try:
            out.append(load_application(data_dir, fname[:-5]))
        except Exception:
            continue
    return out


def delete_application(data_dir: str, app_id: str) -> None:
    p = _path(data_dir, app_id)
    if os.path.exists(p):
        os.remove(p)
