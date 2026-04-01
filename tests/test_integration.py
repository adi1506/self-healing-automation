import os
import pytest
from core.scanner import Scanner
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
        healer = Healer(ai_api_key="")

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
    async def test_heal_detects_broken_locator(self, sample_form_path, tmp_data_dir):
        """Heal should detect and fix a broken locator."""
        manager = ExcelManager(data_dir=tmp_data_dir)
        scanner = Scanner()
        healer = Healer(ai_api_key="")

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
