import os
import tempfile
import pytest
from core.field_rules import FieldRulesStore


@pytest.fixture
def tmp_data_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestFieldRulesStore:
    def test_returns_empty_dict_when_no_sidecar(self, tmp_data_dir):
        store = FieldRulesStore(data_dir=tmp_data_dir)
        rules = store.read("https://example.com/form")
        assert rules == {}

    def test_round_trip_save_and_read(self, tmp_data_dir):
        store = FieldRulesStore(data_dir=tmp_data_dir)
        url = "https://example.com/form"
        store.save(url, {"email": "Always Gmail", "city": "Always Mumbai"})
        rules = store.read(url)
        assert rules == {"email": "Always Gmail", "city": "Always Mumbai"}

    def test_save_overwrites_existing(self, tmp_data_dir):
        store = FieldRulesStore(data_dir=tmp_data_dir)
        url = "https://example.com/form"
        store.save(url, {"email": "Always Gmail"})
        store.save(url, {"email": "Always Yahoo", "city": "Mumbai"})
        rules = store.read(url)
        assert rules == {"email": "Always Yahoo", "city": "Mumbai"}

    def test_two_urls_have_independent_rules(self, tmp_data_dir):
        store = FieldRulesStore(data_dir=tmp_data_dir)
        store.save("https://a.com/form", {"email": "rule a"})
        store.save("https://b.com/form", {"email": "rule b"})
        assert store.read("https://a.com/form") == {"email": "rule a"}
        assert store.read("https://b.com/form") == {"email": "rule b"}

    def test_save_empty_dict_removes_sidecar_or_writes_empty(self, tmp_data_dir):
        store = FieldRulesStore(data_dir=tmp_data_dir)
        url = "https://example.com/form"
        store.save(url, {"email": "Always Gmail"})
        store.save(url, {})
        assert store.read(url) == {}
