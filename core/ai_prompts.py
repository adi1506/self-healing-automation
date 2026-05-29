"""Prompt builders for AIService. Pure functions, no I/O, easily testable.

PROMPT_VERSION participates in the response cache key — bump on any template
change to invalidate stale entries.
"""
from __future__ import annotations

PROMPT_VERSION = "3"


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
    if field.get("available_options"):
        parts.append(f"allowed_options=[{field['available_options']}]")
    if field.get("pattern"): parts.append(f"pattern={field['pattern']}")
    if field.get("maxlength"): parts.append(f"maxlength={field['maxlength']}")
    if field.get("minlength"): parts.append(f"minlength={field['minlength']}")
    if field.get("min") not in ("", None): parts.append(f"min={field['min']}")
    if field.get("max") not in ("", None): parts.append(f"max={field['max']}")
    if field.get("required"): parts.append("required")
    if field.get("autocomplete"): parts.append(f"autocomplete={field['autocomplete']}")
    return ", ".join(parts)


def _field_line(field: dict) -> str:
    """One-line description of a field for inclusion in a row-generation prompt.

    Includes type + constraints (notably allowed_options) so the model can't
    invent values outside the enumerated set.
    """
    name = field.get("element_name", "")
    etype = field.get("element_type", "")
    constraints = _summarize_constraints(field) or "none"
    label = field.get("locator_label") or field.get("placeholder") or ""
    label_part = f", label='{label}'" if label else ""
    return f"  - {name} (type={etype}{label_part}, constraints: {constraints})"


def build_test_cases_for_recording_prompt(
    overridable_steps: list[dict],
    count: int,
    focus_areas: list[str],
    *,
    app_context: str = "",
    screen_context: str = "",
    fixed_fields: list[dict] | None = None,
) -> str:
    """Prompt the model to emit `count` test-case variants for a recording.

    `overridable_steps`: each {action, value, attributes, field_context}. The
      model references these by INDEX (never by id) and may override their
      values; the service maps index -> fingerprint after parsing.
    `fixed_fields`: locked/credential fields, shown read-only as {label, value}
      so the model keeps other fields consistent with them but cannot change
      them.
    `app_context` / `screen_context`: domain + screen/route context — essential
      for Flutter apps where DOM constraints are absent.
    Regression mode is active when "Regression Testing" is in `focus_areas`.
    """
    fixed_fields = fixed_fields or []
    regression = "Regression Testing" in (focus_areas or [])

    def _label(attrs: dict) -> str:
        return (attrs.get("aria_label") or attrs.get("nearest_label_text")
                or attrs.get("placeholder") or attrs.get("name")
                or attrs.get("id") or attrs.get("text_content") or "field")

    def _constraints(attrs: dict) -> str:
        h5 = attrs.get("html5_constraints") or {}
        parts = []
        if h5.get("required") or attrs.get("is_required"): parts.append("required")
        if h5.get("pattern"): parts.append(f"pattern={h5['pattern']}")
        if h5.get("maxlength"): parts.append(f"maxlen={h5['maxlength']}")
        if h5.get("minlength"): parts.append(f"minlen={h5['minlength']}")
        if h5.get("min") not in ("", None): parts.append(f"min={h5['min']}")
        if h5.get("max") not in ("", None): parts.append(f"max={h5['max']}")
        if attrs.get("autocomplete"): parts.append(f"autocomplete={attrs['autocomplete']}")
        return ", ".join(parts)

    lines = []
    for i, step in enumerate(overridable_steps):
        attrs = step.get("attributes") or {}
        cons = _constraints(attrs) or "none"
        line = (f"  [{i}] {_label(attrs)} (type={attrs.get('type', '') or 'n/a'}; "
                f"recorded={step.get('value')!r}; constraints: {cons})")
        ctx = (step.get("field_context") or "").strip()
        if ctx:
            line += f"\n        field-context: {ctx}"
        lines.append(line)
    listing = "\n".join(lines) or "  (no overridable fields)"

    fixed_block = ""
    if fixed_fields:
        fixed_lines = "\n".join(
            f"  - {f.get('label', 'field')} = {f.get('value')!r} (FIXED — never change)"
            for f in fixed_fields
        )
        fixed_block = (
            "\nThese fields are FIXED at their recorded values. Do NOT include them "
            "in overrides; keep your other values consistent with them:\n"
            f"{fixed_lines}\n"
        )

    header = ""
    if app_context.strip():
        header += f"Application context: {app_context.strip()}\n"
    if screen_context.strip():
        header += f"Screen: {screen_context.strip()}\n"

    if regression:
        focus_instruction = (
            "Focus: REGRESSION. Produce realistic, production-like VALID data. "
            "Every variant MUST have expected_outcome='success'. Vary EVERY "
            "overridable field below with a distinct, plausible value (do not "
            "leave fields at their recorded value). Make each variant a different "
            "realistic persona consistent with the application context."
        )
    else:
        focus_text = ", ".join(focus_areas) if focus_areas else "any kind"
        focus_instruction = (
            f"Generate variants focused on: {focus_text}. expected_outcome is "
            "'failure' when the variant should be rejected (validation error, "
            "denied auth, server reject) and 'success' otherwise. Honor "
            "constraints when generating boundary/invalid values (exceed maxlen "
            "on purpose, violate pattern on purpose)."
        )

    return f"""You are a test automation assistant generating variant test cases for a recorded UI flow.
{header}{fixed_block}
Overridable data-entry fields (reference them by INDEX in brackets):
{listing}

Generate exactly {count} test case variants.
{focus_instruction}
Rules:
- Override ONLY the indexed fields above. A field you don't override keeps its recorded value — don't include it.
- Give each case a short human name (e.g. "Senior NRI applicant", "Empty username").
- If a field has a field-context note, follow it exactly.

Respond ONLY with valid JSON in this exact format:
{{"cases": [
  {{"name": "<short name>", "expected_outcome": "success"|"failure",
    "overrides": [{{"step_index": <int>, "value": "<string>"}}],
    "rationale": "<one sentence>"}}
]}}
"""


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
    field_listing = "\n".join(_field_line(f) for f in field_defs) or "  (no fields)"
    existing_summary = "\n".join(
        f"  Row {i+1}: " + ", ".join(f"{k}={v}" for k, v in r.items() if k in field_names)
        for i, r in enumerate(existing_rows)
    ) or "  (no existing rows)"
    return (
        "You are generating ONE complementary test-data row for a web form.\n"
        f"Batch context: {batch_context}\n"
        "Fields (respect every constraint — for select/radio/checkbox you MUST "
        "pick one of the listed allowed_options verbatim, never invent new ones; "
        "for checkbox the values are 'checked' or 'unchecked'):\n"
        f"{field_listing}\n"
        f"Existing rows in this dataset (do not duplicate):\n{existing_summary}\n"
        f"This is row #{row_position} of the new batch — make it distinct from "
        "both existing rows and the other rows in this batch.\n"
        "Also produce a short, human-readable test name (max 6 words) that "
        "describes what makes THIS row distinctive within the batch context "
        "(e.g. 'Senior male from Bangalore').\n"
        "Return strict JSON only:\n"
        '{"name": "<short row name>", "values": {"<field_name>": "<value>"}}'
    )


def build_summarize_run_prompt(run_record: dict) -> str:
    name = run_record.get("scenario_name") or run_record.get("name") or "(unnamed)"
    steps = run_record.get("steps", [])
    step_lines = []
    for i, s in enumerate(steps, start=1):
        outcome = s.get("outcome", "?")
        err = s.get("error", "")
        action = s.get("action", "")
        target = s.get("target", "")
        line = f"  Step {i}: {action} {target} -> {outcome}"
        if err:
            line += f" — {err}"
        step_lines.append(line)
    heals = run_record.get("healings", [])
    heal_summary = (
        f"\nHealings during this run ({len(heals)}):\n"
        + "\n".join(
            f"  - {h.get('element_name', '?')}: {h.get('healed_by', '')}"
            for h in heals
        )
        if heals else ""
    )
    return (
        "Summarize this failed test run in one short paragraph (max ~80 words). "
        "State what failed, the likely root cause, and whether healings affected the outcome.\n"
        f"Scenario: {name}\n"
        "Steps:\n"
        + "\n".join(step_lines)
        + heal_summary
        + "\nReturn strict JSON: {\"summary\": \"<one paragraph>\"}"
    )


def build_suggest_scenarios_prompt(page: dict) -> str:
    elements = page.get("elements", [])
    listing = "\n".join(
        f"  - {e.get('element_name', '')} ({e.get('element_type', '')})"
        for e in elements
    ) or "  (no elements)"
    title = page.get("title") or page.get("url") or "(untitled page)"
    return (
        "Propose 6 distinct test scenarios for the page below. Mix happy-path "
        "and edge-case personas. Each scenario gets a short name, an ai_context "
        "(plain-English persona/scenario sentence), and a one-line rationale.\n"
        f"Page: {title}\n"
        f"Fields:\n{listing}\n"
        'Return strict JSON: {"scenarios": [{"name": "...", "ai_context": "...", '
        '"rationale": "..."}, ...]}'
    )
