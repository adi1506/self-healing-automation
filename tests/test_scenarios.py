import os
import tempfile
import pytest
from core.scenarios import (
    Scenario, save_scenario, load_scenario, list_scenarios, delete_scenario,
    ScenarioValidationError,
)


def test_round_trip_single_page_scenario(tmp_path):
    sc = Scenario(
        id="login_valid",
        name="Login valid",
        kind="single-page",
        base_url="https://example.com/login",
        steps=[{"action": "fill", "target": "email", "value": "a@b.co"}],
        dataset=[],
        expected_outcome="success",
    )
    save_scenario(str(tmp_path), sc)
    loaded = load_scenario(str(tmp_path), "login_valid")
    assert loaded.name == "Login valid"
    assert loaded.kind == "single-page"
    assert loaded.steps[0]["target"] == "email"
    assert loaded.dataset == []


def test_list_and_delete(tmp_path):
    sc = Scenario(
        id="x", name="X", kind="single-page",
        base_url="https://e.com", steps=[{"action": "click", "target": "btn"}],
        dataset=[], expected_outcome="success",
    )
    save_scenario(str(tmp_path), sc)
    assert "x" in [s.id for s in list_scenarios(str(tmp_path))]
    delete_scenario(str(tmp_path), "x")
    assert list_scenarios(str(tmp_path)) == []


def test_validation_rejects_empty_steps(tmp_path):
    sc = Scenario(
        id="bad", name="Bad", kind="single-page",
        base_url="https://e.com", steps=[], dataset=[], expected_outcome="success",
    )
    try:
        save_scenario(str(tmp_path), sc)
    except ScenarioValidationError:
        return
    raise AssertionError("expected ScenarioValidationError")


def test_multi_page_scenario_stores_recipe_refs(tmp_path):
    sc = Scenario(
        id="journey", name="Login journey", kind="multi-page",
        base_url="", steps=[], dataset=[],
        expected_outcome="success", recipe_refs=["Login_Recipe", "Dashboard_Check"],
    )
    save_scenario(str(tmp_path), sc)
    loaded = load_scenario(str(tmp_path), "journey")
    assert loaded.recipe_refs == ["Login_Recipe", "Dashboard_Check"]


def test_multi_page_scenario_with_pages_round_trips(tmp_path):
    sc = Scenario(
        id="journey", name="Login + checkout", kind="multi-page",
        base_url="", steps=[], dataset=[],
        expected_outcome="success",
        pages=[
            {
                "base_url": "https://e.com/login",
                "steps": [{"action": "fill", "target": "email", "value": "a@b.co"}],
                "dataset": [],
                "transition": {
                    "target": "submit_button", "wait_for": "url_contains",
                    "value": "/profile", "timeout_ms": 30000,
                },
            },
            {
                "base_url": "https://e.com/profile",
                "steps": [{"action": "click", "target": "logout"}],
                "dataset": [],
            },
        ],
    )
    save_scenario(str(tmp_path), sc)
    loaded = load_scenario(str(tmp_path), "journey")
    assert loaded.kind == "multi-page"
    assert len(loaded.pages) == 2
    assert loaded.pages[0]["transition"]["value"] == "/profile"
    assert "transition" not in loaded.pages[1]


def test_multi_page_rejects_empty_pages(tmp_path):
    sc = Scenario(
        id="bad", name="Bad", kind="multi-page",
        base_url="", steps=[], dataset=[],
        expected_outcome="success", pages=[],
    )
    try:
        save_scenario(str(tmp_path), sc)
    except ScenarioValidationError:
        return
    raise AssertionError("expected ScenarioValidationError for empty pages")


def test_multi_page_rejects_non_last_page_without_transition(tmp_path):
    sc = Scenario(
        id="bad2", name="Bad", kind="multi-page",
        base_url="", steps=[], dataset=[],
        expected_outcome="success",
        pages=[
            {"base_url": "https://e.com/a", "steps": [], "dataset": []},
            {"base_url": "https://e.com/b", "steps": [], "dataset": []},
        ],
    )
    try:
        save_scenario(str(tmp_path), sc)
    except ScenarioValidationError:
        return
    raise AssertionError("expected ScenarioValidationError for missing transition")


def test_multi_page_rejects_page_without_base_url(tmp_path):
    sc = Scenario(
        id="bad3", name="Bad", kind="multi-page",
        base_url="", steps=[], dataset=[],
        expected_outcome="success",
        pages=[{"base_url": "", "steps": [], "dataset": []}],
    )
    try:
        save_scenario(str(tmp_path), sc)
    except ScenarioValidationError:
        return
    raise AssertionError("expected ScenarioValidationError for missing base_url")


def test_legacy_multi_page_with_recipe_refs_still_loads(tmp_path):
    """Back-compat: old multi-page scenarios that only have recipe_refs must
    still load (validation passes when pages[] is empty AND recipe_refs is
    populated). They simply won't run via the new runner."""
    sc = Scenario(
        id="legacy", name="Legacy journey", kind="multi-page",
        base_url="", steps=[], dataset=[],
        expected_outcome="success", recipe_refs=["A", "B"],
    )
    save_scenario(str(tmp_path), sc)
    loaded = load_scenario(str(tmp_path), "legacy")
    assert loaded.recipe_refs == ["A", "B"]
    assert loaded.pages == []


def test_recorded_scenario_round_trip(tmp_path):
    sc = Scenario(
        id="sc-rec-1",
        name="KYC happy path",
        kind="recorded",
        base_url="",
        steps=[],
        dataset=[],
        expected_outcome="success",
        application_id="app-1",
        recordings=[{"id": "rec-001", "name": "Happy path"}],
        ai_test_cases=[],
    )
    save_scenario(str(tmp_path), sc)
    loaded = load_scenario(str(tmp_path), "sc-rec-1")
    assert loaded.kind == "recorded"
    assert loaded.application_id == "app-1"
    assert loaded.recordings == [{"id": "rec-001", "name": "Happy path"}]


def test_recorded_scenario_requires_application_id(tmp_path):
    sc = Scenario(
        id="sc-rec-2", name="x", kind="recorded", base_url="",
        steps=[], dataset=[], expected_outcome="success",
        application_id=None, recordings=[{"id": "r"}],
    )
    with pytest.raises(ScenarioValidationError):
        save_scenario(str(tmp_path), sc)


def test_recorded_scenario_requires_at_least_one_recording(tmp_path):
    sc = Scenario(
        id="sc-rec-3", name="x", kind="recorded", base_url="",
        steps=[], dataset=[], expected_outcome="success",
        application_id="app-1", recordings=[],
    )
    with pytest.raises(ScenarioValidationError):
        save_scenario(str(tmp_path), sc)
