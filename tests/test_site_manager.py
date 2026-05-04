import os
import json
import pytest
from core.site_manager import SiteManager


@pytest.fixture
def sm(tmp_path):
    return SiteManager(data_dir=str(tmp_path))


def test_register_and_list_sites(sm):
    sm.register_site("https://app.xyz.com", ["https://app.xyz.com/", "https://app.xyz.com/login"])
    sites = sm.list_sites()
    assert sites == ["https://app.xyz.com"]


def test_get_site_pages_returns_registered_pages(sm):
    pages = ["https://app.xyz.com/", "https://app.xyz.com/login"]
    sm.register_site("https://app.xyz.com", pages)
    assert sm.get_site_pages("https://app.xyz.com") == pages


def test_get_site_pages_unknown_site_returns_empty(sm):
    assert sm.get_site_pages("https://nope.com") == []


def test_register_overwrites_previous_entry(sm):
    sm.register_site("https://app.xyz.com", ["https://app.xyz.com/a"])
    sm.register_site("https://app.xyz.com", ["https://app.xyz.com/a", "https://app.xyz.com/b"])
    assert sm.get_site_pages("https://app.xyz.com") == ["https://app.xyz.com/a", "https://app.xyz.com/b"]


def test_delete_site_removes_manifest(sm):
    sm.register_site("https://app.xyz.com", ["https://app.xyz.com/"])
    sm.delete_site("https://app.xyz.com")
    assert sm.list_sites() == []


def test_manifest_records_crawled_at_and_base_url(sm, tmp_path):
    sm.register_site("https://app.xyz.com", ["https://app.xyz.com/"])
    files = [f for f in os.listdir(tmp_path) if f.endswith(".json")]
    assert len(files) == 1
    with open(os.path.join(tmp_path, files[0])) as f:
        data = json.load(f)
    assert data["base_url"] == "https://app.xyz.com"
    assert data["pages"] == ["https://app.xyz.com/"]
    assert "crawled_at" in data
