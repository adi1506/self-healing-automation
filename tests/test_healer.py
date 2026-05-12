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
    return Healer()


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


from unittest.mock import patch as _patch9, MagicMock as _MagicMock9
from core.healer import Healer as _Healer9
from core.ai_service import reset_ai_service as _reset9


def test_heal_records_ai_rationale_in_changes():
    _reset9()
    h = _Healer9()
    h.scanner = _MagicMock9()
    h.scanner.scan.return_value = [
        {"sno": 1, "element_name": "Given Name", "element_type": "input-text",
         "locator_id": "given_name", "locator_name": "given_name",
         "locator_css": "", "locator_xpath": "", "locator_data_testid": "",
         "locator_label": "Given Name", "placeholder": "", "available_options": ""},
    ]
    em = _MagicMock9()
    em.read_element_map.return_value = [
        {"sno": 1, "element_name": "First Name", "element_type": "input-text",
         "locator_id": "first_name", "locator_name": "first_name",
         "locator_css": "", "locator_xpath": "", "locator_data_testid": "",
         "locator_label": "First Name", "placeholder": "", "available_options": ""},
    ]
    with _patch9.object(h.ai_matcher, "is_available", return_value=True), \
         _patch9.object(h.ai_matcher, "match_element",
                      return_value={"match_index": 0, "confidence": 0.93,
                                    "reasoning": "Both fields request a given name."}):
        report = h.heal("http://example.com", em)

    ai_change = next(c for c in report["changes"] if "Level 3" in c["healed_by"])
    assert ai_change["rationale"] == "Both fields request a given name."
    assert ai_change["confidence"] == 0.93
