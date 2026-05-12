"""Prompt builders for AIService. Pure functions, no I/O, easily testable.

PROMPT_VERSION participates in the response cache key — bump on any template
change to invalidate stale entries.
"""
from __future__ import annotations

PROMPT_VERSION = "1"


def build_match_prompt(old_element: dict, candidates: list[dict]) -> str:
    candidates_text = ""
    for i, c in enumerate(candidates):
        candidates_text += (
            f"  Index {i}: name='{c.get('element_name', '')}', "
            f"type='{c.get('element_type', '')}', "
            f"placeholder='{c.get('placeholder', '')}', "
            f"label='{c.get('locator_label', '')}'\n"
        )
    return f"""You are a test automation assistant. An element on a web page has changed and we need to find its new version.

The OLD element had these properties:
  name='{old_element.get('element_name', '')}'
  type='{old_element.get('element_type', '')}'
  placeholder='{old_element.get('placeholder', '')}'
  label='{old_element.get('locator_label', '')}'

These are the CURRENT unmatched elements on the page:
{candidates_text}
Which current element (by index) is most likely the same field as the old element?
Consider semantic meaning, not just exact text matches. For example, "First Name" and "Given Name" are the same field.

Respond ONLY with valid JSON in this exact format:
{{"match_index": <index or -1 if no match>, "confidence": <0.0 to 1.0>, "reasoning": "<brief explanation>"}}
"""


def build_recipe_prompt(page_url: str, elements: list[dict], goal: str) -> str:
    listing = ""
    for i, e in enumerate(elements):
        listing += (
            f"  Index {i}: name='{e.get('element_name', '')}', "
            f"type='{e.get('element_type', '')}', "
            f"placeholder='{e.get('placeholder', '')}', "
            f"label='{e.get('locator_label', '')}'\n"
        )
    return f"""You are a test automation assistant.
Goal: {goal}
Page URL: {page_url}
Available elements on this page (refer to them by INDEX only):
{listing}
Output a JSON list of steps to achieve the goal. Each step must reference
an element by INDEX from the list above. Allowed actions: fill, click, select, check.

For sensitive fields (passwords, OTPs, credit cards), use the placeholder
"<USER_FILLS>" as the value.

Respond ONLY with valid JSON in this exact format:
{{"steps": [{{"action": "fill", "element_index": 0, "value": "..."}}], "reasoning": "<brief>"}}
"""


def _summarize_constraints(field: dict) -> str:
    parts = []
    if field.get("pattern"): parts.append(f"pattern={field['pattern']}")
    if field.get("maxlength"): parts.append(f"maxlength={field['maxlength']}")
    if field.get("minlength"): parts.append(f"minlength={field['minlength']}")
    if field.get("min") not in ("", None): parts.append(f"min={field['min']}")
    if field.get("max") not in ("", None): parts.append(f"max={field['max']}")
    if field.get("required"): parts.append("required")
    if field.get("autocomplete"): parts.append(f"autocomplete={field['autocomplete']}")
    return ", ".join(parts)


def build_field_value_prompt(
    field: dict, page_context: dict, per_field_rule: str, ai_context: str,
) -> str:
    constraints = _summarize_constraints(field)
    ctx_line = ". ".join(
        v for v in (page_context.get("title", ""),
                    page_context.get("h1", ""),
                    page_context.get("first_paragraph", "")) if v
    ) or "none"
    return (
        "You are generating one value for a single form field.\n"
        f"Page context: {ctx_line}\n"
        f"Field label: {field.get('locator_label') or field.get('element_name', '')}\n"
        f"Field name: {field.get('locator_name', '')}\n"
        f"Field type: {field.get('element_type', '')}\n"
        f"Helper text: {field.get('helper_text') or 'none'}\n"
        f"DOM constraints: {constraints or 'none'}\n"
        f"Per-field rule: {per_field_rule or 'none'}\n"
        f"Test case scenario: {ai_context or 'default valid value'}\n"
        "Return strict JSON only: {\"value\": \"<generated value>\"}"
    )


def build_refine_row_prompt(
    field_defs: list[dict], current_row: dict[str, str],
    refine_prompt: str, locked: list[str],
) -> str:
    field_lines = []
    for f in field_defs:
        name = f.get("element_name", "")
        if name in locked:
            field_lines.append(
                f"  - {name} (LOCKED, must not change): current='{current_row.get(name, '')}', "
                f"type={f.get('element_type', '')}"
            )
        else:
            field_lines.append(
                f"  - {name}: current='{current_row.get(name, '')}', "
                f"type={f.get('element_type', '')}"
            )
    listing = "\n".join(field_lines)
    return (
        "You are adjusting a single test-data row for a web form.\n"
        f"User instruction: {refine_prompt}\n"
        "Fields and current values:\n"
        f"{listing}\n"
        "Return strict JSON only. Output every field name as a key with its NEW value. "
        "Fields marked LOCKED MUST keep their current value exactly. "
        "Other fields should change only if the user instruction implies a change.\n"
        '{"values": {"<field_name>": "<value>"}}'
    )


def build_complementary_row_prompt(
    field_defs: list[dict], existing_rows: list[dict],
    batch_context: str, row_position: int,
) -> str:
    field_names = [f.get("element_name", "") for f in field_defs]
    existing_summary = "\n".join(
        f"  Row {i+1}: " + ", ".join(f"{k}={v}" for k, v in r.items() if k in field_names)
        for i, r in enumerate(existing_rows)
    ) or "  (no existing rows)"
    return (
        "You are generating ONE complementary test-data row for a web form.\n"
        f"Batch context: {batch_context}\n"
        f"Fields: {', '.join(field_names)}\n"
        f"Existing rows in this dataset (do not duplicate):\n{existing_summary}\n"
        f"This is row #{row_position} of the new batch — make it distinct from "
        "both existing rows and the other rows in this batch.\n"
        "Return strict JSON only with every field as a key:\n"
        '{"values": {"<field_name>": "<value>"}}'
    )
