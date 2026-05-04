import os
import pytest
from core.crawler import Crawler, _is_same_domain, _normalize_url, _is_crawlable_url


def test_is_same_domain_matches_netloc():
    assert _is_same_domain("https://app.xyz.com/login", "https://app.xyz.com/")
    assert _is_same_domain("https://app.xyz.com:8080/x", "https://app.xyz.com/")
    assert not _is_same_domain("https://other.com/x", "https://app.xyz.com/")


def test_is_crawlable_url_filters_non_html_and_schemes():
    assert _is_crawlable_url("https://x.com/page")
    assert _is_crawlable_url("https://x.com/page.html")
    assert not _is_crawlable_url("mailto:a@b.com")
    assert not _is_crawlable_url("tel:+1234")
    assert not _is_crawlable_url("javascript:void(0)")
    assert not _is_crawlable_url("https://x.com/file.pdf")
    assert not _is_crawlable_url("https://x.com/style.css")
    assert not _is_crawlable_url("https://x.com/img.png")


def test_normalize_url_drops_fragment_and_trailing_slash():
    assert _normalize_url("https://X.com/path/#frag") == "https://x.com/path"
    assert _normalize_url("https://x.com/") == "https://x.com"
    assert _normalize_url("HTTPS://X.com/PATH") == "https://x.com/PATH"


@pytest.fixture
def site_base_url():
    return "file://" + os.path.abspath("test_form/site/index.html").replace("\\", "/")


class TestCrawler:
    @pytest.mark.asyncio
    async def test_crawl_finds_all_same_domain_pages(self, site_base_url):
        crawler = Crawler()
        pages = await crawler.crawl_async(site_base_url, max_pages=10, max_depth=3)
        urls = sorted(p["url"] for p in pages)
        assert any("index.html" in u for u in urls)
        assert any("about.html" in u for u in urls)
        assert any("contact.html" in u for u in urls)
        assert not any("external.example.com" in u for u in urls)

    @pytest.mark.asyncio
    async def test_crawl_extracts_elements_per_page(self, site_base_url):
        crawler = Crawler()
        pages = await crawler.crawl_async(site_base_url, max_pages=10, max_depth=3)
        contact = next(p for p in pages if "contact.html" in p["url"])
        names = [e["element_name"] for e in contact["elements"]]
        assert "Name" in names
        assert "Message" in names
        assert "Send" in names

    @pytest.mark.asyncio
    async def test_crawl_respects_max_pages(self, site_base_url):
        crawler = Crawler()
        pages = await crawler.crawl_async(site_base_url, max_pages=2, max_depth=3)
        assert len(pages) <= 2
