import os
import pytest
from core.scanner import Scanner


@pytest.fixture
def sample_form_path():
    """Return the absolute path to the sample form HTML file."""
    return "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")


@pytest.fixture
def scanner():
    return Scanner()


class TestScanElements:
    @pytest.mark.asyncio
    async def test_extracts_all_elements(self, scanner, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        assert len(elements) >= 10

    @pytest.mark.asyncio
    async def test_captures_input_text(self, scanner, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        first_name = next((e for e in elements if e["element_name"] == "First Name"), None)
        assert first_name is not None
        assert first_name["element_type"] == "input-text"
        assert first_name["locator_id"] == "#firstName"
        assert first_name["locator_name"] == "firstName"
        assert first_name["locator_data_testid"] == "first-name-input"
        assert first_name["placeholder"] == "Enter first name"

    @pytest.mark.asyncio
    async def test_captures_select_dropdown(self, scanner, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        gender = next((e for e in elements if e["element_name"] == "Gender"), None)
        assert gender is not None
        assert gender["element_type"] == "select"
        assert "Male" in gender["available_options"]
        assert "Female" in gender["available_options"]
        assert "Other" in gender["available_options"]

    @pytest.mark.asyncio
    async def test_captures_radio_group(self, scanner, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        employment = next((e for e in elements if "Employment" in e["element_name"]), None)
        assert employment is not None
        assert employment["element_type"] == "radio"
        assert "Employed" in employment["available_options"]
        assert "Unemployed" in employment["available_options"]
        assert "Student" in employment["available_options"]

    @pytest.mark.asyncio
    async def test_captures_checkbox(self, scanner, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        terms = next((e for e in elements if "Terms" in e["element_name"]), None)
        assert terms is not None
        assert terms["element_type"] == "checkbox"

    @pytest.mark.asyncio
    async def test_captures_textarea(self, scanner, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        address = next((e for e in elements if e["element_name"] == "Address"), None)
        assert address is not None
        assert address["element_type"] == "textarea"

    @pytest.mark.asyncio
    async def test_captures_button(self, scanner, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        submit = next((e for e in elements if "Register" in e.get("element_name", "")), None)
        assert submit is not None
        assert submit["element_type"] == "button"

    @pytest.mark.asyncio
    async def test_captures_xpath(self, scanner, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        first_name = next((e for e in elements if e["element_name"] == "First Name"), None)
        assert first_name["locator_xpath"] != ""

    @pytest.mark.asyncio
    async def test_captures_css_selector(self, scanner, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        first_name = next((e for e in elements if e["element_name"] == "First Name"), None)
        assert first_name["locator_css"] != ""

    @pytest.mark.asyncio
    async def test_sno_auto_increments(self, scanner, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        snos = [e["sno"] for e in elements]
        assert snos == list(range(1, len(elements) + 1))

    @pytest.mark.asyncio
    async def test_all_elements_have_new_status(self, scanner, sample_form_path):
        elements = await scanner.scan(sample_form_path)
        for elem in elements:
            assert elem["status"] == "NEW"
