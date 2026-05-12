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
    import time
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s._available = True
    s._available_at = time.monotonic()
    return s


def test_generate_complementary_rows_returns_n_rows(svc):
    field_defs = [
        {"element_name": "name", "element_type": "input-text"},
        {"element_name": "city", "element_type": "input-text"},
    ]
    responses = [
        {"response": '{"values": {"name": "A", "city": "Paris"}}'},
        {"response": '{"values": {"name": "B", "city": "Berlin"}}'},
        {"response": '{"values": {"name": "C", "city": "Madrid"}}'},
    ]
    with patch.object(svc.client, "generate", side_effect=responses):
        rows = svc.generate_complementary_rows(
            field_defs, existing_rows=[], batch_context="EU customers", n=3,
        )
    assert len(rows) == 3
    assert {r["city"] for r in rows} == {"Paris", "Berlin", "Madrid"}


def test_generate_complementary_rows_skips_invalid_responses(svc):
    field_defs = [{"element_name": "x", "element_type": "input-text"}]
    responses = [
        {"response": '{"values": {"x": "a"}}'},
        {"response": "garbage"},
        {"response": '{"values": {"x": "c"}}'},
    ]
    with patch.object(svc.client, "generate", side_effect=responses):
        rows = svc.generate_complementary_rows(field_defs, [], "ctx", n=3)
    # Two valid rows survive; the garbage one is dropped.
    assert len(rows) == 2
    assert {r["x"] for r in rows} == {"a", "c"}


def test_generate_complementary_rows_returns_empty_when_unavailable(tmp_path):
    s = AIService(settings_path=str(tmp_path / "s.yaml"))
    s.client = None
    rows = s.generate_complementary_rows(
        [{"element_name": "x"}], [], "ctx", n=3,
    )
    assert rows == []
