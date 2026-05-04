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


from core.setter import Setter


class RecipeExecutor:
    """Executes recipe steps against a Playwright page using the existing locator chain."""

    def __init__(self, elements_by_page: dict[str, list[dict]]):
        """
        elements_by_page maps a page URL → its scanned element list. Used to translate
        a step's `target` (element_name) into the locator dict that Setter._find_element expects.
        """
        self.elements_by_page = elements_by_page
        self._setter = Setter()

    def _resolve_element(self, recipe: dict, target_name: str) -> dict | None:
        elements = self.elements_by_page.get(recipe["start_url"], [])
        for elem in elements:
            if elem.get("element_name") == target_name:
                return elem
        return None

    async def execute(self, page, recipe: dict) -> dict:
        validate_recipe(recipe)
        step_results = []
        any_step_failed = False

        for idx, step in enumerate(recipe["steps"]):
            action = step["action"]
            try:
                if step.get("value") == SENSITIVE_PLACEHOLDER:
                    raise RuntimeError(
                        f"step references unfilled <USER_FILLS> placeholder for target "
                        f"'{step.get('target')}' — supply a value before running"
                    )

                if action in ("fill", "click", "select", "check"):
                    elem = self._resolve_element(recipe, step["target"])
                    if elem is None:
                        raise RuntimeError(
                            f"target '{step['target']}' not in scanned elements for "
                            f"{recipe['start_url']}"
                        )
                    handle = await self._setter._find_element(page, elem)
                    if handle is None:
                        raise RuntimeError(f"could not locate '{step['target']}' on page")

                    if action == "fill":
                        await self._setter._set_value(page, handle, elem, step["value"])
                    elif action == "click":
                        await handle.click()
                    elif action == "select":
                        await self._setter._set_value(page, handle, elem, step["value"])
                    elif action == "check":
                        is_checked = await handle.is_checked()
                        if not is_checked:
                            await handle.click()

                elif action == "wait_for_url":
                    await page.wait_for_url(f"**{step['contains']}**", timeout=10000)

                elif action == "wait_for_selector":
                    await page.wait_for_selector(step["selector"], timeout=10000)

                step_results.append({"step_idx": idx, "status": "PASS", "error": None})
            except Exception as exc:
                step_results.append({"step_idx": idx, "status": "FAIL", "error": str(exc)})
                any_step_failed = True

        assertion_results = []
        any_assertion_failed = False
        for ai, assertion in enumerate(recipe.get("assertions", [])):
            atype = assertion["type"]
            try:
                if atype == "url_contains":
                    actual_url = page.url
                    if assertion["value"] not in actual_url:
                        raise AssertionError(f"url '{actual_url}' does not contain '{assertion['value']}'")
                elif atype == "element_visible":
                    handle = await page.query_selector(assertion.get("selector") or assertion.get("target"))
                    if handle is None:
                        raise AssertionError(f"element '{assertion}' not found")
                    visible = await handle.is_visible()
                    if not visible:
                        raise AssertionError(f"element '{assertion}' not visible")
                elif atype == "element_contains_text":
                    selector = assertion.get("selector") or assertion.get("target")
                    handle = await page.query_selector(selector)
                    if handle is None:
                        raise AssertionError(f"element '{selector}' not found")
                    text = (await handle.inner_text()).strip()
                    if assertion["value"] not in text:
                        raise AssertionError(
                            f"text '{text}' does not contain '{assertion['value']}'"
                        )
                assertion_results.append({"idx": ai, "status": "PASS", "detail": ""})
            except Exception as exc:
                assertion_results.append({"idx": ai, "status": "FAIL", "detail": str(exc)})
                any_assertion_failed = True

        actual_outcome = (
            "failure"
            if (any_step_failed or any_assertion_failed)
            else "success"
        )
        return {
            "step_results": step_results,
            "assertion_results": assertion_results,
            "expected_outcome": recipe["expected_outcome"],
            "actual_outcome": actual_outcome,
            "outcome_match": actual_outcome == recipe["expected_outcome"],
        }
