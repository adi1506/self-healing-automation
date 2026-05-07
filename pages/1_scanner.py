import streamlit as st
from datetime import datetime
from core.scanner import Scanner
from core.crawler import Crawler
from core.excel_manager import ExcelManager
from core.site_manager import SiteManager

st.set_page_config(page_title="Scanner", layout="wide")
st.title("Website Scanner")

DATA_DIR = "data/scans"
excel_manager = ExcelManager(data_dir=DATA_DIR)
site_manager = SiteManager(data_dir="data/sites")
scanner = Scanner()
crawler = Crawler(scanner=scanner)

url = st.text_input("Target URL", placeholder="https://your-app.com/form")
crawl_site = st.checkbox(
    "Crawl entire site (same-domain)", value=False,
    help="Walk all reachable pages on the same domain and scan each one.",
)
col_a, col_b = st.columns(2)
with col_a:
    max_pages = st.number_input("Max pages", min_value=1, max_value=500, value=50, disabled=not crawl_site)
with col_b:
    max_depth = st.number_input("Max depth", min_value=1, max_value=10, value=5, disabled=not crawl_site)

if st.button("Scan", type="primary", disabled=not url):
    if crawl_site:
        with st.spinner("Crawling site and scanning each page..."):
            pages = crawler.crawl(url, max_pages=int(max_pages), max_depth=int(max_depth))

        if not pages:
            st.warning("Crawl returned no pages.")
        else:
            page_urls = []
            for p in pages:
                if p.get("error"):
                    st.warning(f"Skipped {p['url']}: {p['error']}")
                    continue
                if p["elements"]:
                    excel_manager.save_element_map(p["url"], p["elements"])
                    scan_id = f"SCAN-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    excel_manager.append_scan_history(p["url"], {
                        "scan_id": scan_id,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "total_elements": len(p["elements"]),
                        "new": len(p["elements"]),
                        "changed": 0,
                        "removed": 0,
                        "unchanged": 0,
                    })
                page_urls.append(p["url"])

            site_manager.register_site(url, page_urls)
            st.success(f"Crawled {len(page_urls)} pages.")
            for p in pages:
                with st.expander(f"{p['url']} — {len(p.get('elements', []))} elements"):
                    if p.get("elements"):
                        st.dataframe(
                            [{"Name": e["element_name"], "Type": e["element_type"]} for e in p["elements"]],
                            use_container_width=True,
                        )
                    else:
                        st.text("No elements scanned.")
    else:
        with st.spinner("Scanning page..."):
            result = scanner.scan_with_context(url)
            elements = result["elements"]
            page_context = result["page_context"]

        if elements:
            excel_manager.save_element_map(url, elements)
            excel_manager.save_page_context(url, page_context)
            scan_id = f"SCAN-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            excel_manager.append_scan_history(url, {
                "scan_id": scan_id,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_elements": len(elements),
                "new": len(elements),
                "changed": 0,
                "removed": 0,
                "unchanged": 0,
            })

            st.success(f"Found {len(elements)} elements!")
            display_data = [{
                "S.No": e["sno"], "Element Name": e["element_name"],
                "Type": e["element_type"], "ID": e.get("locator_id", ""),
                "Name": e.get("locator_name", ""), "Data-TestID": e.get("locator_data_testid", ""),
                "Status": e["status"],
            } for e in elements]
            st.dataframe(display_data, use_container_width=True)

            excel_path = excel_manager.get_excel_path(url)
            with open(excel_path, "rb") as f:
                st.download_button(
                    label="Download Excel",
                    data=f.read(),
                    file_name=f"scan_{excel_manager.sanitize_url(url)}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
        else:
            st.warning("No interactive elements found on the page.")

st.divider()
st.subheader("Scanned URLs")
scanned_urls = excel_manager.list_scanned_urls()
for scanned_url in scanned_urls:
    col1, col2 = st.columns([5, 1])
    col1.text(scanned_url)
    if col2.button("Delete", key=f"del_{scanned_url}"):
        excel_manager.delete_url(scanned_url)
        st.success(f"Deleted: {scanned_url}")
        st.rerun()

sites = site_manager.list_sites()
if sites:
    st.subheader("Crawled Sites")
    for s in sites:
        c1, c2 = st.columns([5, 1])
        c1.text(f"{s} — {len(site_manager.get_site_pages(s))} pages")
        if c2.button("Delete", key=f"site_del_{s}"):
            site_manager.delete_site(s)
            st.success(f"Deleted site: {s}")
            st.rerun()
