from __future__ import annotations

import yaml


VALID_ACTIONS = {"fill", "click", "select", "check", "wait_for_url", "wait_for_selector"}
VALID_ASSERTIONS = {"url_contains", "element_visible", "element_contains_text"}
VALID_OUTCOMES = {"success", "failure"}
SENSITIVE_PLACEHOLDER = "<USER_FILLS>"


class RecipeValidationError(ValueError):
    """Raised when a recipe fails schema validation."""


def validate_recipe(recipe: dict) -> None:
    for required in ("name", "start_url", "steps", "expected_outcome"):
        if required not in recipe:
            raise RecipeValidationError(f"Missing required field: {required}")
    if recipe["expected_outcome"] not in VALID_OUTCOMES:
        raise RecipeValidationError(
            f"expected_outcome must be one of {VALID_OUTCOMES}, got {recipe['expected_outcome']}"
        )
    if not isinstance(recipe["steps"], list) or not recipe["steps"]:
        raise RecipeValidationError("steps must be a non-empty list")
    for i, step in enumerate(recipe["steps"]):
        action = step.get("action")
        if action not in VALID_ACTIONS:
            raise RecipeValidationError(
                f"steps[{i}]: action must be one of {VALID_ACTIONS}, got {action}"
            )
        if action in ("fill", "click", "select", "check") and "target" not in step:
            raise RecipeValidationError(f"steps[{i}]: action '{action}' requires 'target'")
        if action in ("fill", "select") and "value" not in step:
            raise RecipeValidationError(f"steps[{i}]: action '{action}' requires 'value'")
        if action == "wait_for_url" and "contains" not in step:
            raise RecipeValidationError(f"steps[{i}]: action 'wait_for_url' requires 'contains'")
        if action == "wait_for_selector" and "selector" not in step:
            raise RecipeValidationError(
                f"steps[{i}]: action 'wait_for_selector' requires 'selector'"
            )
    for i, assertion in enumerate(recipe.get("assertions", [])):
        atype = assertion.get("type")
        if atype not in VALID_ASSERTIONS:
            raise RecipeValidationError(
                f"assertions[{i}]: type must be one of {VALID_ASSERTIONS}, got {atype}"
            )


def save_recipe(path: str, recipe: dict) -> None:
    validate_recipe(recipe)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(recipe, f, sort_keys=False)


def load_recipe(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_flow(path: str, flow: dict) -> None:
    for required in ("name", "recipes", "expected_outcome"):
        if required not in flow:
            raise RecipeValidationError(f"Flow missing required field: {required}")
    if flow["expected_outcome"] not in VALID_OUTCOMES:
        raise RecipeValidationError(f"Flow expected_outcome invalid: {flow['expected_outcome']}")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(flow, f, sort_keys=False)


def load_flow(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
