import os
import pytest
from core.recipes import (
    save_recipe, load_recipe, validate_recipe,
    save_flow, load_flow,
    RecipeValidationError,
)


def _good_recipe():
    return {
        "name": "login_valid",
        "goal": "log in successfully",
        "start_url": "https://app.xyz.com/login",
        "steps": [
            {"action": "fill", "target": "Email", "value": "test@x.com"},
            {"action": "fill", "target": "Password", "value": "<USER_FILLS>"},
            {"action": "click", "target": "Sign In"},
        ],
        "assertions": [
            {"type": "url_contains", "value": "/dashboard"},
        ],
        "expected_outcome": "success",
    }


def test_round_trip_recipe(tmp_path):
    path = str(tmp_path / "r.yaml")
    save_recipe(path, _good_recipe())
    loaded = load_recipe(path)
    assert loaded == _good_recipe()


def test_validate_passes_on_good_recipe():
    validate_recipe(_good_recipe())


def test_validate_rejects_unknown_action():
    bad = _good_recipe()
    bad["steps"][0]["action"] = "explode"
    with pytest.raises(RecipeValidationError):
        validate_recipe(bad)


def test_validate_rejects_missing_start_url():
    bad = _good_recipe()
    del bad["start_url"]
    with pytest.raises(RecipeValidationError):
        validate_recipe(bad)


def test_validate_rejects_unknown_outcome():
    bad = _good_recipe()
    bad["expected_outcome"] = "maybe"
    with pytest.raises(RecipeValidationError):
        validate_recipe(bad)


def test_validate_rejects_unknown_assertion_type():
    bad = _good_recipe()
    bad["assertions"][0]["type"] = "looks_pretty"
    with pytest.raises(RecipeValidationError):
        validate_recipe(bad)


def test_save_recipe_validates_before_writing(tmp_path):
    path = str(tmp_path / "r.yaml")
    bad = _good_recipe()
    bad["expected_outcome"] = "maybe"
    with pytest.raises(RecipeValidationError):
        save_recipe(path, bad)
    assert not os.path.exists(path)


def test_round_trip_flow(tmp_path):
    flow = {
        "name": "full_login_flow",
        "recipes": ["login_valid", "navigate_dashboard"],
        "expected_outcome": "success",
    }
    path = str(tmp_path / "f.yaml")
    save_flow(path, flow)
    assert load_flow(path) == flow
