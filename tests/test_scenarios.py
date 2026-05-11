import os
import tempfile
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
