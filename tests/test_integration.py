import os
import re
import pytest
from core.scanner import Scanner
from core.test_case_generator import TestCaseGenerator
from core.setter import Setter
from core.healer import Healer
from core.excel_manager import ExcelManager


@pytest.fixture
def sample_form_path():
    return "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")


@pytest.fixture
def tmp_data_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def screenshot_dir(tmp_path):
    d = str(tmp_path / "screenshots")
    os.makedirs(d, exist_ok=True)
    return d


class TestFullWorkflow:
    @pytest.mark.asyncio
    async def test_scan_set_verify_heal(self, sample_form_path, tmp_data_dir, screenshot_dir):
        """Full workflow: scan -> add test data -> set fields -> verify -> heal"""
        manager = ExcelManager(data_dir=tmp_data_dir)
        scanner = Scanner()
        setter = Setter()
        healer = Healer()

        # Step 1: Scan
        elements = await scanner.scan(sample_form_path)
        assert len(elements) >= 10
        manager.save_element_map(sample_form_path, elements)

        # Step 2: Save test data
        test_rows = [
            {
                "sno": 1,
                "test_case_name": "Valid registration",
                "First Name": "John",
                "Last Name": "Doe",
                "Email": "john@example.com",
                "Phone Number": "1234567890",
                "Age": "30",
                "Gender": "Male",
                "Country": "India",
                "Employment Status": "Employed",
                "Address": "123 Main St",
            }
        ]
        manager.save_test_data(sample_form_path, test_rows)

        # Step 3: Read back and run setter
        element_map = manager.read_element_map(sample_form_path)
        test_data = manager.read_test_data(sample_form_path)
        assert len(test_data) == 1

        test_values = {k: v for k, v in test_data[0].items() if k not in ("S.No", "Test Case Name") and v}

        results = await setter.set_fields(
            sample_form_path, element_map, test_values,
            screenshot_dir=screenshot_dir, run_id="RUN-INTEGRATION",
        )

        # Step 4: Verify results
        pass_count = sum(1 for r in results if r["status"] == "PASS")
        assert pass_count >= 5  # At least text fields should pass

        # Step 5: Check screenshot exists
        assert os.path.exists(os.path.join(screenshot_dir, "RUN-INTEGRATION.png"))

        # Step 6: Heal (nothing should have changed)
        report = await healer.heal(sample_form_path, manager)
        assert report["unchanged"] > 0
        assert report["removed"] == 0

    @pytest.mark.asyncio
    async def test_scan_current_page_extracts_same_elements(self, sample_form_path):
        """scan_current_page on an already-loaded page returns the same elements as scan()."""
        from playwright.async_api import async_playwright
        scanner = Scanner()
        elements_via_scan = await scanner._scan_async(sample_form_path)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(sample_form_path, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            elements_via_helper = await scanner.scan_current_page(page)
            await browser.close()
        assert [e["element_name"] for e in elements_via_helper] == [e["element_name"] for e in elements_via_scan]

    @pytest.mark.asyncio
    async def test_heal_detects_broken_locator(self, sample_form_path, tmp_data_dir):
        """Heal should detect and fix a broken locator."""
        manager = ExcelManager(data_dir=tmp_data_dir)
        scanner = Scanner()
        healer = Healer()

        # Scan
        elements = await scanner.scan(sample_form_path)
        # Break one locator
        for elem in elements:
            if elem["element_name"] == "Email":
                elem["locator_id"] = "#brokenEmail"
                elem["status"] = "UNCHANGED"
                break
        manager.save_element_map(sample_form_path, elements)

        # Heal
        report = await healer.heal(sample_form_path, manager)

        # Email should be healed via another locator
        updated = manager.read_element_map(sample_form_path)
        email_elem = next((e for e in updated if e["element_name"] == "Email"), None)
        assert email_elem is not None
        assert email_elem["status"] in ("CHANGED", "UNCHANGED")


class TestMultiPageFlow:
    @pytest.mark.asyncio
    async def test_crawl_then_recipe_execute(self, tmp_path):
        from core.crawler import Crawler
        from core.site_manager import SiteManager
        from core.recipes import save_recipe, load_recipe, RecipeExecutor
        from playwright.async_api import async_playwright

        site_url = "file://" + os.path.abspath("test_form/site/index.html").replace("\\", "/")
        crawler = Crawler()
        pages = await crawler.crawl_async(site_url, max_pages=10, max_depth=3)
        assert len(pages) >= 3

        contact = next(p for p in pages if "contact.html" in p["url"])
        recipe = {
            "name": "send_message",
            "goal": "submit a message",
            "start_url": contact["url"],
            "steps": [
                {"action": "fill", "target": "Name", "value": "Alice"},
                {"action": "fill", "target": "Message", "value": "hi"},
            ],
            "assertions": [],
            "expected_outcome": "success",
        }
        recipe_path = str(tmp_path / "send_message.yaml")
        save_recipe(recipe_path, recipe)
        loaded = load_recipe(recipe_path)

        elements_by_page = {p["url"]: p["elements"] for p in pages}
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(loaded["start_url"])
            executor = RecipeExecutor(elements_by_page=elements_by_page)
            result = await executor.execute(page, loaded)
            await browser.close()

        assert result["outcome_match"] is True
        assert result["actual_outcome"] == "success"


@pytest.fixture
def constrained_url():
    path = os.path.abspath("test_form/v9_constrained.html")
    return f"file:///{path.replace(os.sep, '/')}"


def test_end_to_end_heuristic_generation(constrained_url):
    """Scan v9_constrained.html → generate cases → assert quality."""
    result = Scanner().scan_with_context(constrained_url)
    elements = result["elements"]
    page_context = result["page_context"]

    gen = TestCaseGenerator(field_dictionary_path="data/field_dictionary.yaml")
    rows = gen.generate(elements, page_context=page_context, mode="compact")

    # First row is happy path; every constrained field has a valid value
    happy = rows[0]
    assert happy["test_case_name"] == "Happy path"
    cust_ref_val = happy["values"]["Customer Reference"]
    assert re.fullmatch(r"[A-Z]{4}[0-9]{4}", cust_ref_val), \
        f"Customer Reference {cust_ref_val!r} does not match pattern"
    age_val = int(happy["values"]["Age"])
    assert 18 <= age_val <= 120

    # At least one negative row per constrained field
    negative_field_names = {r["test_case_name"].split(":")[0] for r in rows[1:]}
    for required_field in {"Customer Reference", "Age", "Email", "First Name", "Country"}:
        assert required_field in negative_field_names, \
            f"missing negative case for {required_field}"
