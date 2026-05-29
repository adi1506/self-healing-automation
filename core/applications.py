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
    # Free-text domain/business description the user fills once per app, fed to
    # the AI when generating test data — especially valuable for Flutter apps
    # (e.g. mCAS) where the DOM exposes almost no field metadata.
    domain_context: Optional[str] = None

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


def delete_application_cascade(
    apps_dir: str,
    scenarios_dir: str,
    storage_states_dir: str,
    work_dir: str,
    app_id: str,
) -> dict:
    """Delete an application together with the scenarios that reference it,
    its storage-state blob, and its recorder_work scratch files.

    Idempotent: calling it twice for the same app_id returns
    {"scenarios_deleted": 0, "test_cases_deleted": 0, "files_removed": []}
    on the second call.

    Returns a summary dict so the UI can show counts in a confirmation toast.
    Replay screenshots under data/replay_runs/ are intentionally NOT removed —
    there is no back-reference index from recording_id to scenario_id and the
    disk cost is low.
    """
    from core.scenarios import list_scenarios_for_app, delete_scenario

    files_removed: list[str] = []

    matching = list_scenarios_for_app(scenarios_dir, app_id)
    test_cases_deleted = sum(len(s.ai_test_cases or []) for s in matching)
    for sc in matching:
        delete_scenario(scenarios_dir, sc.id)
        files_removed.append(os.path.join(scenarios_dir, f"{sc.id}.yaml"))

    state_path = os.path.join(storage_states_dir, f"{app_id}.enc")
    if os.path.exists(state_path):
        os.remove(state_path)
        files_removed.append(state_path)

    if os.path.isdir(work_dir):
        for fname in os.listdir(work_dir):
            if fname.startswith(f"{app_id}_"):
                fpath = os.path.join(work_dir, fname)
                try:
                    os.remove(fpath)
                    files_removed.append(fpath)
                except FileNotFoundError:
                    pass

    delete_application(apps_dir, app_id)
    files_removed.append(os.path.join(apps_dir, f"{app_id}.yaml"))

    return {
        "scenarios_deleted": len(matching),
        "test_cases_deleted": test_cases_deleted,
        "files_removed": files_removed,
    }
