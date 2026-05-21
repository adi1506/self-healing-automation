import streamlit as st
from ui.library.scan_form import render as render_scan
from ui.library.list import render as render_list

st.set_page_config(page_title="Library", layout="wide")
st.title("Library")
st.caption("Scanned pages are reusable assets. Scenarios reference them by URL.")

st.subheader("Scan new page / Crawl site")
render_scan()

st.divider()
render_list()
