import os
from pathlib import Path

from core.applications import (
    Application, save_application, delete_application_cascade,
)
from core.scenarios import Scenario, save_scenario, list_scenarios


def _make_recorded(app_id: str, sid: str, *, test_case_count: int = 0) -> Scenario:
    return Scenario(
        id=sid, name=sid, kind="recorded",
        base_url="", steps=[], dataset=[], expected_outcome="success",
        application_id=app_id,
        recordings=[{"id": "r1", "name": "n", "steps": [], "start_url": ""}],
        ai_test_cases=[
            {"id": f"tc{i}", "name": f"case {i}", "recording_id": "r1",
             "expected_outcome": "success", "overrides": {}}
            for i in range(test_case_count)
        ],
    )


def test_cascade_deletes_app_and_its_scenarios(tmp_path):
    apps_dir = tmp_path / "apps"
    scns_dir = tmp_path / "scns"
    states_dir = tmp_path / "states"
    work_dir = tmp_path / "work"
    for d in (apps_dir, scns_dir, states_dir, work_dir):
        d.mkdir()

    save_application(str(apps_dir), Application(
        id="app-1", name="A", base_url_pattern="a.com",
    ))
    save_application(str(apps_dir), Application(
        id="app-2", name="B", base_url_pattern="b.com",
    ))
    save_scenario(str(scns_dir), _make_recorded("app-1", "s1", test_case_count=2))
    save_scenario(str(scns_dir), _make_recorded("app-1", "s2", test_case_count=3))
    save_scenario(str(scns_dir), _make_recorded("app-2", "s3", test_case_count=1))
    (states_dir / "app-1.enc").write_bytes(b"state")
    (work_dir / "app-1_login.yaml").write_text("k: v")
    (work_dir / "app-1_candidates.json").write_text("{}")
    (work_dir / "app-2_login.yaml").write_text("k: v")

    summary = delete_application_cascade(
        str(apps_dir), str(scns_dir), str(states_dir), str(work_dir), "app-1",
    )

    assert summary["scenarios_deleted"] == 2
    assert summary["test_cases_deleted"] == 5
    assert not (apps_dir / "app-1.yaml").exists()
    assert not (states_dir / "app-1.enc").exists()
    assert not (work_dir / "app-1_login.yaml").exists()
    assert not (work_dir / "app-1_candidates.json").exists()
    # Other app's data is untouched
    assert (apps_dir / "app-2.yaml").exists()
    assert (work_dir / "app-2_login.yaml").exists()
    remaining = [s.id for s in list_scenarios(str(scns_dir))]
    assert remaining == ["s3"]


def test_cascade_is_idempotent(tmp_path):
    apps_dir = tmp_path / "apps"
    scns_dir = tmp_path / "scns"
    states_dir = tmp_path / "states"
    work_dir = tmp_path / "work"
    for d in (apps_dir, scns_dir, states_dir, work_dir):
        d.mkdir()
    save_application(str(apps_dir), Application(
        id="app-1", name="A", base_url_pattern="a.com",
    ))

    delete_application_cascade(
        str(apps_dir), str(scns_dir), str(states_dir), str(work_dir), "app-1",
    )
    # Second call must not raise
    summary = delete_application_cascade(
        str(apps_dir), str(scns_dir), str(states_dir), str(work_dir), "app-1",
    )
    assert summary["scenarios_deleted"] == 0
    assert summary["test_cases_deleted"] == 0


def test_cascade_leaves_replay_runs_alone(tmp_path):
    apps_dir = tmp_path / "apps"
    scns_dir = tmp_path / "scns"
    states_dir = tmp_path / "states"
    work_dir = tmp_path / "work"
    replay_dir = tmp_path / "replay_runs"
    for d in (apps_dir, scns_dir, states_dir, work_dir, replay_dir):
        d.mkdir()
    save_application(str(apps_dir), Application(
        id="app-1", name="A", base_url_pattern="a.com",
    ))
    save_scenario(str(scns_dir), _make_recorded("app-1", "s1"))
    # Pretend a replay produced a screenshot under recording id "r1"
    rec_dir = replay_dir / "r1"
    rec_dir.mkdir()
    (rec_dir / "step0.png").write_bytes(b"png")

    delete_application_cascade(
        str(apps_dir), str(scns_dir), str(states_dir), str(work_dir), "app-1",
    )
    # The cascade must not touch data/replay_runs
    assert (replay_dir / "r1" / "step0.png").exists()
