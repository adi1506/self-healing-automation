from core.ai_prompts import (
    build_match_prompt,
    build_recipe_prompt,
    build_field_value_prompt,
    PROMPT_VERSION,
)


def test_prompt_version_is_string():
    assert isinstance(PROMPT_VERSION, str)
    assert len(PROMPT_VERSION) > 0


def test_match_prompt_lists_candidates_by_index():
    old = {"element_name": "First Name", "element_type": "input-text",
           "placeholder": "", "locator_label": "First Name"}
    candidates = [
        {"element_name": "Email", "element_type": "input-email",
         "placeholder": "", "locator_label": "Email"},
        {"element_name": "Given Name", "element_type": "input-text",
         "placeholder": "", "locator_label": "Given Name"},
    ]
    prompt = build_match_prompt(old, candidates)
    assert "First Name" in prompt
    assert "Index 0" in prompt and "Index 1" in prompt
    assert "Given Name" in prompt
    assert "match_index" in prompt


def test_recipe_prompt_includes_goal_and_url():
    prompt = build_recipe_prompt(
        "https://example.com/signup",
        [{"element_name": "Email", "element_type": "input-email",
          "placeholder": "", "locator_label": ""}],
        "register a new user",
    )
    assert "register a new user" in prompt
    assert "https://example.com/signup" in prompt
    assert "Index 0" in prompt


def test_field_value_prompt_includes_constraints():
    field = {
        "element_name": "PAN", "element_type": "input-text",
        "locator_label": "PAN", "locator_name": "pan",
        "pattern": "[A-Z]{5}[0-9]{4}[A-Z]", "maxlength": "10",
        "required": True,
    }
    prompt = build_field_value_prompt(
        field, {"title": "KYC", "h1": "Identity", "first_paragraph": ""},
        per_field_rule="Use a valid PAN", ai_context="Indian resident",
    )
    assert "PAN" in prompt
    assert "[A-Z]{5}[0-9]{4}[A-Z]" in prompt
    assert "maxlength=10" in prompt
    assert "Indian resident" in prompt
    assert "Use a valid PAN" in prompt
    assert "\"value\"" in prompt
