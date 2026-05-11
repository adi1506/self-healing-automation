import os
import yaml
from openpyxl import Workbook
from core.scenarios import list_scenarios
from core.scenario_migration import migrate_all


def _write_recipe(path: str, name: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({
            "name": name,
            "start_url": "https://example.com/login",
            "steps": [{"action": "fill", "target": "email", "value": "a@b.co"}],
            "expected_outcome": "success",
        }, f)


def _write_flow(path: str, name: str, recipes: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({
            "name": name, "recipes": recipes, "expected_outcome": "success",
        }, f)


def test_migrates_recipe_to_single_page_scenario(tmp_path):
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    _write_recipe(str(recipes_dir / "login_valid.yaml"), "login_valid")
    scenarios_dir = tmp_path / "scenarios"

    report = migrate_all(
        recipes_dir=str(recipes_dir), flows_dir=str(tmp_path / "flows"),
        scans_dir=str(tmp_path / "scans"), scenarios_dir=str(scenarios_dir),
    )

    scs = list_scenarios(str(scenarios_dir))
    assert len(scs) == 1
    assert scs[0].kind == "single-page"
    assert scs[0].name == "login_valid"
    assert scs[0].base_url == "https://example.com/login"
    assert report["recipes_migrated"] == 1


def test_migrates_flow_to_multi_page_scenario(tmp_path):
    recipes_dir = tmp_path / "recipes"
    flows_dir = tmp_path / "flows"
    recipes_dir.mkdir(); flows_dir.mkdir()
    _write_recipe(str(recipes_dir / "step1.yaml"), "step1")
    _write_recipe(str(recipes_dir / "step2.yaml"), "step2")
    _write_flow(str(flows_dir / "journey.yaml"), "journey", ["step1", "step2"])
    scenarios_dir = tmp_path / "scenarios"

    migrate_all(
        recipes_dir=str(recipes_dir), flows_dir=str(flows_dir),
        scans_dir=str(tmp_path / "scans"), scenarios_dir=str(scenarios_dir),
    )

    multi = [s for s in list_scenarios(str(scenarios_dir)) if s.kind == "multi-page"]
    assert len(multi) == 1
    assert multi[0].recipe_refs == ["step1", "step2"]


def test_is_idempotent(tmp_path):
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    _write_recipe(str(recipes_dir / "x.yaml"), "x")
    scenarios_dir = tmp_path / "scenarios"

    migrate_all(
        recipes_dir=str(recipes_dir), flows_dir=str(tmp_path / "flows"),
        scans_dir=str(tmp_path / "scans"), scenarios_dir=str(scenarios_dir),
    )
    n1 = len(list_scenarios(str(scenarios_dir)))

    migrate_all(
        recipes_dir=str(recipes_dir), flows_dir=str(tmp_path / "flows"),
        scans_dir=str(tmp_path / "scans"), scenarios_dir=str(scenarios_dir),
    )
    n2 = len(list_scenarios(str(scenarios_dir)))
    assert n1 == n2 == 1


def test_migrates_test_data_grid_to_dataset(tmp_path):
    # Build a minimal scan workbook with element_map + test_data sheets.
    scans_dir = tmp_path / "scans"
    scans_dir.mkdir()
    wb = Workbook()
    em = wb.active
    em.title = "Element Map"
    em.append(["S.No", "Element Name", "Element Type"])
    em.append([1, "email", "input"])
    em.append([2, "submit", "button"])
    td = wb.create_sheet("Test Data")
    td.append(["S.No", "Test Case Name", "AI Context", "Expected Outcome", "email"])
    td.append([1, "happy", "", "success", "a@b.co"])
    td.append([2, "bad email", "", "failure", "bad"])
    wb.save(str(scans_dir / "example_com.xlsx"))

    scenarios_dir = tmp_path / "scenarios"
    migrate_all(
        recipes_dir=str(tmp_path / "recipes"), flows_dir=str(tmp_path / "flows"),
        scans_dir=str(scans_dir), scenarios_dir=str(scenarios_dir),
    )

    dd = [s for s in list_scenarios(str(scenarios_dir))
          if s.kind == "single-page" and s.dataset]
    assert len(dd) == 1
    assert len(dd[0].dataset) == 2
    assert dd[0].dataset[0]["email"] == "a@b.co"
