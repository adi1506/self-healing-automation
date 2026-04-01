import os
import streamlit as st

st.set_page_config(
    page_title="Self-Healing Test Automation",
    page_icon="🔧",
    layout="wide",
)

st.title("Self-Healing Test Automation")
st.markdown("""
Welcome to the Self-Healing Test Automation tool. Use the sidebar to navigate:

- **Scanner** — Scan a web page and extract all form elements
- **Test Data** — Manage test case data for scanned pages
- **Runner** — Populate forms and verify values
- **Heal Report** — View self-healing results and change detection
- **History** — Browse past scans, runs, and heal logs
""")

# Sidebar: Gemini API key configuration
st.sidebar.divider()
st.sidebar.subheader("Settings")
gemini_key = st.sidebar.text_input(
    "Gemini API Key",
    value=os.environ.get("GEMINI_API_KEY", ""),
    type="password",
    help="Optional. Used for AI-powered self-healing (Level 3). Leave empty to skip AI matching.",
)
if gemini_key:
    st.session_state.gemini_api_key = gemini_key
