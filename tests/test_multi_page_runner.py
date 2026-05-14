"""End-to-end test for the multi-page runner.

Drives two local HTML pages in one Playwright session:
  Page A: fills `email`, clicks `Continue` -> navigates to Page B
  Page B: fills `phone`, run ends.

The fixture uses a button with an inline JS onclick rather than a real
<a href> so the transition target is a real scanned element with an
element_name (buttons are scanned but not editable, so they're a valid
transition target).
"""
from __future__ import annotations

import pathlib

from core.excel_manager import ExcelManager
from core.scanner import Scanner
from core.scenarios import Scenario
from ui.scenarios.detail import _run_multi_page_scenario


FIXTURE_A = pathlib.Path(__file__).parent.parent / "test_form" / "page_a.html"
FIXTURE_B = pathlib.Path(__file__).parent.parent / "test_form" / "page_b.html"


def _file_url(p: pathlib.Path) -> str:
    return p.absolute().as_uri()


def test_multi_page_runner_walks_two_pages(tmp_path, monkeypatch):
    if not FIXTURE_A.exists() or not FIXTURE_B.exists():
        import pytest
        pytest.skip("fixtures missing")

    # Use a tmp scans dir so we don't pollute real data.
    monkeypatch.chdir(tmp_path)
    scans_dir = tmp_path / "data" / "scans"
    scans_dir.mkdir(parents=True)

    # Scan both pages and persist element maps so the runner can resolve targets.
    em = ExcelManager(data_dir=str(scans_dir))
    scanner = Scanner()
    url_a = _file_url(FIXTURE_A)
    url_b = _file_url(FIXTURE_B)
    elements_a = scanner.scan(url_a)
    elements_b = scanner.scan(url_b)
    em.save_element_map(url_a, elements_a)
    em.save_element_map(url_b, elements_b)

    # Find the button element_name on page A — that's our transition target.
    button_a = next(e for e in elements_a if e["element_type"] == "button")

    sc = Scenario(
        id="two_page", name="Two-page journey", kind="multi-page",
        base_url="", steps=[], dataset=[],
        expected_outcome="success",
        pages=[
            {
                "base_url": url_a,
                "steps": [],
                "dataset": [{"__expected_outcome": "success", "email": "a@b.co"}],
                "transition": {
                    "target": button_a["element_name"],
                    "wait_for": "url_contains",
                    "value": "page_b.html",
                    "timeout_ms": 30000,
                },
            },
            {
                "base_url": url_b,
                "steps": [],
                "dataset": [{"__expected_outcome": "success", "phone": "5551234"}],
            },
        ],
    )

    result = _run_multi_page_scenario(sc, data_scans_dir=str(scans_dir))
    assert result["mode"] == "multi-page"
    assert len(result["page_outcomes"]) == 2
    assert result["page_outcomes"][0]["page_status"] == "PASS"
    assert result["page_outcomes"][0]["transition_status"] == "PASS"
    assert result["page_outcomes"][1]["page_status"] == "PASS"
    assert result["scenario_status"] == "PASS"
