import os
import pytest
from core.setter import Setter
from core.scanner import Scanner
from core.excel_manager import ExcelManager


@pytest.fixture
def sample_form_path():
    return "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")


@pytest.fixture
def tmp_data_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def manager(tmp_data_dir):
    return ExcelManager(data_dir=tmp_data_dir)


@pytest.fixture
def setter():
    return Setter()


@pytest.fixture
def scanner():
    return Scanner()


class TestSetFields:
    @pytest.mark.asyncio
    async def test_sets_text_input(self, setter, scanner, manager, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        manager.save_element_map(sample_form_path, elements)
        element_map = manager.read_element_map(sample_form_path)

        test_data = {"First Name": "John"}
        results = await setter.set_fields(sample_form_path, element_map, test_data)

        first_name_result = next((r for r in results if r["element_name"] == "First Name"), None)
        assert first_name_result is not None
        assert first_name_result["expected_value"] == "John"
        assert first_name_result["actual_value"] == "John"
        assert first_name_result["status"] == "PASS"

    @pytest.mark.asyncio
    async def test_sets_dropdown(self, setter, scanner, manager, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        manager.save_element_map(sample_form_path, elements)
        element_map = manager.read_element_map(sample_form_path)

        test_data = {"Gender": "Male"}
        results = await setter.set_fields(sample_form_path, element_map, test_data)

        gender_result = next((r for r in results if r["element_name"] == "Gender"), None)
        assert gender_result is not None
        assert gender_result["actual_value"] == "Male"
        assert gender_result["status"] == "PASS"

    def test_select_value_case_insensitive(self, setter, scanner, manager, sample_form_path):
        """If the supplied label only differs in case from a real option, fill it anyway.
        Scenarios authored by users/AI often case-normalize ("male" / "MALE"), and the
        live DOM keeps the canonical "Male". Forcing the user to fix every casing
        difference defeats the point of self-healing."""
        elements = scanner.scan(sample_form_path)
        manager.save_element_map(sample_form_path, elements)
        element_map = manager.read_element_map(sample_form_path)

        results = setter.set_fields(sample_form_path, element_map, {"Gender": "male"})
        gender = next(r for r in results if r["element_name"] == "Gender")
        assert gender["status"] == "PASS", gender
        assert gender["actual_value"] == "Male"

    def test_select_value_fuzzy_match(self, setter, scanner, manager, sample_form_path):
        """A close-but-not-exact label (trailing whitespace, light typo) should resolve
        rather than blow up with a 30-second Playwright timeout."""
        elements = scanner.scan(sample_form_path)
        manager.save_element_map(sample_form_path, elements)
        element_map = manager.read_element_map(sample_form_path)

        results = setter.set_fields(sample_form_path, element_map, {"Country": "USA "})
        country = next(r for r in results if r["element_name"] == "Country")
        assert country["status"] == "PASS"
        assert country["actual_value"] == "USA"

    def test_select_value_no_match_fails_fast(self, setter, scanner, manager, sample_form_path):
        """When the value is unrelated to any option, fail fast with a useful error
        listing the real options — don't sit in a 30-second Playwright timeout."""
        import time
        elements = scanner.scan(sample_form_path)
        manager.save_element_map(sample_form_path, elements)
        element_map = manager.read_element_map(sample_form_path)

        t0 = time.time()
        results = setter.set_fields(sample_form_path, element_map, {"Country": "Atlantis"})
        elapsed = time.time() - t0
        country = next(r for r in results if r["element_name"] == "Country")
        assert country["status"] == "FAIL"
        assert "Atlantis" in country["actual_value"] or "not in options" in country["actual_value"].lower()
        # Whole 4-field setter run shouldn't take anywhere near Playwright's 30s default.
        assert elapsed < 10, f"select fail took {elapsed:.1f}s — should be <1s with fail-fast"

    @pytest.mark.asyncio
    async def test_sets_radio_button(self, setter, scanner, manager, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        manager.save_element_map(sample_form_path, elements)
        element_map = manager.read_element_map(sample_form_path)

        test_data = {"Employment Status": "Student"}
        results = await setter.set_fields(sample_form_path, element_map, test_data)

        emp_result = next((r for r in results if r["element_name"] == "Employment Status"), None)
        assert emp_result is not None
        assert emp_result["actual_value"] == "Student"
        assert emp_result["status"] == "PASS"

    @pytest.mark.asyncio
    async def test_sets_checkbox(self, setter, scanner, manager, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        manager.save_element_map(sample_form_path, elements)
        element_map = manager.read_element_map(sample_form_path)

        terms_elem = next((e for e in elements if "Terms" in e["element_name"]), None)
        test_data = {terms_elem["element_name"]: "checked"}
        results = await setter.set_fields(sample_form_path, element_map, test_data)

        terms_result = next((r for r in results if "Terms" in r["element_name"]), None)
        assert terms_result is not None
        assert terms_result["actual_value"] == "checked"
        assert terms_result["status"] == "PASS"

    @pytest.mark.asyncio
    async def test_skips_empty_values(self, setter, scanner, manager, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        manager.save_element_map(sample_form_path, elements)
        element_map = manager.read_element_map(sample_form_path)

        test_data = {"First Name": "Jane"}
        results = await setter.set_fields(sample_form_path, element_map, test_data)

        assert len(results) == 1
        assert results[0]["element_name"] == "First Name"

    @pytest.mark.asyncio
    async def test_skips_buttons(self, setter, scanner, manager, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        manager.save_element_map(sample_form_path, elements)
        element_map = manager.read_element_map(sample_form_path)

        test_data = {"First Name": "Test", "Register": "click"}
        results = await setter.set_fields(sample_form_path, element_map, test_data)

        button_result = next((r for r in results if r["element_name"] == "Register"), None)
        assert button_result is None

    @pytest.mark.asyncio
    async def test_takes_screenshot(self, setter, scanner, manager, sample_form_path, tmp_path):
        elements = await scanner.scan(sample_form_path)
        manager.save_element_map(sample_form_path, elements)
        element_map = manager.read_element_map(sample_form_path)

        screenshot_dir = str(tmp_path / "screenshots")
        os.makedirs(screenshot_dir, exist_ok=True)

        test_data = {"First Name": "Screenshot Test"}
        results = await setter.set_fields(
            sample_form_path, element_map, test_data,
            screenshot_dir=screenshot_dir, run_id="RUN-001"
        )

        screenshot_path = os.path.join(screenshot_dir, "RUN-001.png")
        assert os.path.exists(screenshot_path)
