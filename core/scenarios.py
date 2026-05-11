from __future__ import annotations
import os
from dataclasses import dataclass, field, asdict
from typing import Any
import yaml

VALID_KINDS = {"single-page", "multi-page"}
VALID_OUTCOMES = {"success", "failure"}


class ScenarioValidationError(ValueError):
    """Raised when a Scenario fails schema validation."""


@dataclass
class Scenario:
    id: str
    name: str
    kind: str
    base_url: str
    steps: list[dict]
    dataset: list[dict]
    expected_outcome: str
    recipe_refs: list[str] = field(default_factory=list)
    assertions: list[dict] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _scenario_path(data_dir: str, scenario_id: str) -> str:
    return os.path.join(data_dir, f"{scenario_id}.yaml")


def _validate(sc: Scenario) -> None:
    if not sc.id or not sc.id.replace("_", "").replace("-", "").isalnum():
        raise ScenarioValidationError(f"id must be alphanumeric/underscore/hyphen, got {sc.id!r}")
    if sc.kind not in VALID_KINDS:
        raise ScenarioValidationError(f"kind must be in {VALID_KINDS}, got {sc.kind!r}")
    if sc.expected_outcome not in VALID_OUTCOMES:
        raise ScenarioValidationError(
            f"expected_outcome must be in {VALID_OUTCOMES}, got {sc.expected_outcome!r}"
        )
    if sc.kind == "single-page":
        if not sc.steps:
            raise ScenarioValidationError("single-page scenarios must have at least one step")
        if not sc.base_url:
            raise ScenarioValidationError("single-page scenarios require base_url")
    else:  # multi-page
        if not sc.recipe_refs:
            raise ScenarioValidationError("multi-page scenarios require recipe_refs")


def save_scenario(data_dir: str, sc: Scenario) -> None:
    _validate(sc)
    os.makedirs(data_dir, exist_ok=True)
    with open(_scenario_path(data_dir, sc.id), "w", encoding="utf-8") as f:
        yaml.safe_dump(sc.to_dict(), f, sort_keys=False)


def load_scenario(data_dir: str, scenario_id: str) -> Scenario:
    with open(_scenario_path(data_dir, scenario_id), encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Scenario(**data)


def list_scenarios(data_dir: str) -> list[Scenario]:
    if not os.path.isdir(data_dir):
        return []
    out: list[Scenario] = []
    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".yaml") or fname.startswith("_"):
            continue
        try:
            out.append(load_scenario(data_dir, fname[:-5]))
        except Exception:
            continue
    return out


def delete_scenario(data_dir: str, scenario_id: str) -> None:
    p = _scenario_path(data_dir, scenario_id)
    if os.path.exists(p):
        os.remove(p)
