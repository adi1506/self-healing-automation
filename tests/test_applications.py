import pytest
from core.applications import (
    Application, save_application, load_application,
    list_applications, delete_application,
)


def test_application_minimal_round_trip(tmp_path):
    app = Application(
        id="app-finnone",
        name="FinnOne Neo",
        base_url_pattern="10.0.42.28:7256",
    )
    save_application(str(tmp_path), app)
    loaded = load_application(str(tmp_path), "app-finnone")
    assert loaded == app


def test_application_with_login_recording_pointer(tmp_path):
    app = Application(
        id="app-hdb",
        name="HDB Financial",
        base_url_pattern="mcoput.hdbfs.com",
        login_recording_id="login-001",
        storage_state_path="data/storage_states/app-hdb.enc",
        storage_state_captured_at="2026-05-15T10:00:00",
        storage_state_expires_at="2026-05-16T10:00:00",
    )
    save_application(str(tmp_path), app)
    loaded = load_application(str(tmp_path), "app-hdb")
    assert loaded == app


def test_list_and_delete(tmp_path):
    save_application(str(tmp_path), Application(id="a", name="A", base_url_pattern="a.com"))
    save_application(str(tmp_path), Application(id="b", name="B", base_url_pattern="b.com"))
    ids = [a.id for a in list_applications(str(tmp_path))]
    assert sorted(ids) == ["a", "b"]
    delete_application(str(tmp_path), "a")
    ids = [a.id for a in list_applications(str(tmp_path))]
    assert ids == ["b"]


def test_list_empty_dir_returns_empty():
    assert list_applications("/nonexistent/path") == []
