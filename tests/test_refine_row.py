from unittest.mock import patch
import pytest
from core.ai_service import AIService, reset_ai_service


@pytest.fixture(autouse=True)
def _reset():
    reset_ai_service()
    yield
    reset_ai_service()


@pytest.fixture
def svc(tmp_path):
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s._available = True
    import time
    s._available_at = time.monotonic()
    return s


def test_refine_row_updates_only_ai_eligible_fields(svc):
    field_defs = [
        {"element_name": "city", "element_type": "input-text"},
        {"element_name": "pincode", "element_type": "input-text",
         "pattern": r"\d{6}"},
        {"element_name": "email", "element_type": "input-email"},
    ]
    current = {"city": "Mumbai", "pincode": "400001", "email": "a@b.com"}

    fake_response = (
        '{"values": {"city": "Bangalore", "pincode": "999", '
        '"email": "priya@gmail.com"}}'
    )
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": fake_response}
        new_row = svc.refine_row(field_defs, current,
                                 "change to Bangalore with Gmail")

    # Locked field (pincode has a pattern) must be preserved.
    assert new_row["pincode"] == "400001"
    # AI-eligible fields are updated.
    assert new_row["city"] == "Bangalore"
    assert new_row["email"] == "priya@gmail.com"


def test_refine_row_returns_none_when_unavailable(tmp_path):
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s.client = None
    assert s.refine_row([{"element_name": "x"}], {"x": "y"}, "tweak") is None


def test_refine_row_returns_none_on_invalid_response(svc):
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": "not json"}
        result = svc.refine_row(
            [{"element_name": "x", "element_type": "input-text"}],
            {"x": "y"}, "tweak",
        )
    assert result is None
