import os
import os as _os
import pytest
from playwright.async_api import async_playwright
from core.recipes import (
    save_recipe, load_recipe, validate_recipe,
    save_flow, load_flow,
    RecipeValidationError,
    RecipeExecutor,
)
from core.scanner import Scanner


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


@pytest.fixture
def contact_url():
    return "file://" + _os.path.abspath("test_form/site/contact.html").replace("\\", "/")


class TestRecipeExecutor:
    @pytest.mark.asyncio
    async def test_executes_fill_and_click_steps(self, contact_url):
        scanner = Scanner()
        elements = await scanner._scan_async(contact_url)
        recipe = {
            "name": "send_message",
            "start_url": contact_url,
            "steps": [
                {"action": "fill", "target": "Name", "value": "Alice"},
                {"action": "fill", "target": "Message", "value": "Hello"},
            ],
            "assertions": [],
            "expected_outcome": "success",
        }

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(contact_url)
            executor = RecipeExecutor(elements_by_page={contact_url: elements})
            result = await executor.execute(page, recipe)
            await browser.close()

        assert all(s["status"] == "PASS" for s in result["step_results"])
        assert result["actual_outcome"] == "success"
        assert result["outcome_match"] is True

    @pytest.mark.asyncio
    async def test_unfilled_user_placeholder_fails_step(self, contact_url):
        scanner = Scanner()
        elements = await scanner._scan_async(contact_url)
        recipe = {
            "name": "send_message",
            "start_url": contact_url,
            "steps": [
                {"action": "fill", "target": "Name", "value": "<USER_FILLS>"},
            ],
            "assertions": [],
            "expected_outcome": "success",
        }

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(contact_url)
            executor = RecipeExecutor(elements_by_page={contact_url: elements})
            result = await executor.execute(page, recipe)
            await browser.close()

        assert result["step_results"][0]["status"] == "FAIL"
        assert "USER_FILLS" in result["step_results"][0]["error"]

    @pytest.mark.asyncio
    async def test_failure_outcome_passes_when_assertions_match(self, contact_url):
        scanner = Scanner()
        elements = await scanner._scan_async(contact_url)
        recipe = {
            "name": "negative",
            "start_url": contact_url,
            "steps": [
                {"action": "fill", "target": "Name", "value": "Alice"},
            ],
            "assertions": [
                {"type": "url_contains", "value": "thispathwillnevermatch"},
            ],
            "expected_outcome": "failure",
        }

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(contact_url)
            executor = RecipeExecutor(elements_by_page={contact_url: elements})
            result = await executor.execute(page, recipe)
            await browser.close()

        # All steps passed but assertion failed → actual_outcome = failure → matches expected
        assert result["actual_outcome"] == "failure"
        assert result["outcome_match"] is True
