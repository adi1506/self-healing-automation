import streamlit as st
from datetime import datetime
from core.scanner import Scanner
from core.crawler import Crawler
from core.excel_manager import ExcelManager
from core.site_manager import SiteManager

DATA_DIR = "data/scans"


def render():
    excel_manager = ExcelManager(data_dir=DATA_DIR)
    site_manager = SiteManager(data_dir="data/sites")
    scanner = Scanner()
    crawler = Crawler(scanner=scanner)

    url = st.text_input("Target URL", placeholder="https://your-app.com/form", key="scan_url")
    crawl_site = st.checkbox("Crawl entire site (same-domain)", value=False, key="scan_crawl")
    c1, c2 = st.columns(2)
    with c1:
        max_pages = st.number_input("Max pages", 1, 500, 50, disabled=not crawl_site, key="scan_max_pages")
    with c2:
        max_depth = st.number_input("Max depth", 1, 10, 5, disabled=not crawl_site, key="scan_max_depth")
    submitted = st.button("Scan", type="primary", key="scan_submit")

    if not submitted or not url:
        return

    if crawl_site:
        with st.spinner("Crawling site and scanning each page..."):
            pages = crawler.crawl(url, max_pages=int(max_pages), max_depth=int(max_depth))
        if not pages:
            st.warning("Crawl returned no pages.")
            return
        page_urls = []
        for p in pages:
            if p.get("error"):
                st.warning(f"Skipped {p['url']}: {p['error']}")
                continue
            if p["elements"]:
                excel_manager.save_element_map(p["url"], p["elements"])
                excel_manager.append_scan_history(p["url"], {
                    "scan_id": f"SCAN-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "total_elements": len(p["elements"]),
                    "new": len(p["elements"]),
                    "changed": 0, "removed": 0, "unchanged": 0,
                })
            page_urls.append(p["url"])
        site_manager.register_site(url, page_urls)
        st.success(f"Crawled {len(page_urls)} pages.")
    else:
        with st.spinner("Scanning page..."):
            result = scanner.scan_with_context(url)
            elements = result["elements"]
            page_context = result["page_context"]
        if elements:
            excel_manager.save_element_map(url, elements)
            excel_manager.save_page_context(url, page_context)
            excel_manager.append_scan_history(url, {
                "scan_id": f"SCAN-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_elements": len(elements),
                "new": len(elements),
                "changed": 0, "removed": 0, "unchanged": 0,
            })
            st.success(f"Found {len(elements)} elements!")
        else:
            st.warning("No interactive elements found on the page.")
