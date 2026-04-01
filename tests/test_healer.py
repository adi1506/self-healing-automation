import os
import pytest
from unittest.mock import MagicMock, patch
from core.healer import Healer
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
def scanner():
    return Scanner()


@pytest.fixture
def healer():
    return Healer(ai_api_key="")


class TestHealerUnchanged:
    @pytest.mark.asyncio
    async def test_detects_unchanged_elements(self, healer, scanner, manager, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        manager.save_element_map(sample_form_path, elements)

        report = await healer.heal(sample_form_path, manager)
        assert report["unchanged"] > 0
        assert report["changed"] == 0
        assert report["new"] == 0
        assert report["removed"] == 0


class TestHealerDetectsChanges:
    @pytest.mark.asyncio
    async def test_detects_removed_element(self, healer, scanner, manager, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        fake_element = {
            "sno": len(elements) + 1,
            "element_name": "Fake Field",
            "element_type": "input-text",
            "locator_id": "#fakeField",
            "locator_name": "fakeField",
            "locator_css": "#fakeField",
            "locator_xpath": '//*[@id="fakeField"]',
            "locator_data_testid": "fake-field",
            "locator_label": "Fake Field",
            "placeholder": "",
            "available_options": "",
            "current_value": "",
            "status": "UNCHANGED",
            "change_details": "",
            "healed_by": "",
        }
        elements.append(fake_element)
        manager.save_element_map(sample_form_path, elements)

        report = await healer.heal(sample_form_path, manager)
        assert report["removed"] >= 1


class TestHealerLevel1:
    @pytest.mark.asyncio
    async def test_heals_via_alternative_selector(self, healer, scanner, manager, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        for elem in elements:
            if elem["element_name"] == "First Name":
                elem["locator_id"] = "#brokenId"
                elem["status"] = "UNCHANGED"
                break
        manager.save_element_map(sample_form_path, elements)

        report = await healer.heal(sample_form_path, manager)
        changes = report.get("changes", [])
        first_name_change = next((c for c in changes if c["element_name"] == "First Name"), None)
        if first_name_change:
            assert "Level 1" in first_name_change.get("healed_by", "")


class TestHealerAttributeMatching:
    def test_similarity_score_identical(self, healer):
        fp1 = {"element_type": "input-text", "label_text": "First Name", "placeholder": "Enter first name"}
        fp2 = {"element_type": "input-text", "label_text": "First Name", "placeholder": "Enter first name"}
        score = healer.calculate_similarity(fp1, fp2)
        assert score >= 0.95

    def test_similarity_score_different(self, healer):
        fp1 = {"element_type": "input-text", "label_text": "First Name", "placeholder": "Enter first name"}
        fp2 = {"element_type": "select", "label_text": "Country", "placeholder": ""}
        score = healer.calculate_similarity(fp1, fp2)
        assert score < 0.5

    def test_similarity_score_partial_match(self, healer):
        fp1 = {"element_type": "input-text", "label_text": "First Name", "placeholder": "Enter first name"}
        fp2 = {"element_type": "input-text", "label_text": "Given Name", "placeholder": "Enter given name"}
        score = healer.calculate_similarity(fp1, fp2)
        assert 0.3 < score < 0.85


class TestHealReport:
    @pytest.mark.asyncio
    async def test_report_has_required_fields(self, healer, scanner, manager, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        manager.save_element_map(sample_form_path, elements)

        report = await healer.heal(sample_form_path, manager)
        assert "unchanged" in report
        assert "changed" in report
        assert "new" in report
        assert "removed" in report
        assert "changes" in report
        assert "total_elements" in report
