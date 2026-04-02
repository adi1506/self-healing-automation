from dotenv import load_dotenv
import streamlit as st

load_dotenv()

st.set_page_config(
    page_title="Self-Healing Test Automation",
    layout="wide",
)

st.title("Self-Healing Test Automation")
st.markdown("""
Use the sidebar to navigate:

- **Scanner** - Scan a web page and extract all form elements
- **Test Data** - Manage test case data for scanned pages
- **Runner** - Populate forms and verify values
- **Heal Report** - View self-healing results and change detection
- **History** - Browse past scans, runs, and heal logs
""")
