from dotenv import load_dotenv
import streamlit as st
from core.scenario_migration import migrate_all

load_dotenv()

st.set_page_config(
    page_title="Self-Healing Test Automation",
    layout="wide",
)

# Run the one-shot migration on import. It's idempotent + cheap.
migrate_all(
    recipes_dir="data/recipes",
    flows_dir="data/flows",
    scans_dir="data/scans",
    scenarios_dir="data/scenarios",
)

st.title("Self-Healing Test Automation")
st.markdown("""
Use the sidebar to navigate:

- **Dashboard** — health overview and recent runs
- **Scenarios** — build, edit, and run test scenarios (data-driven supported)
- **Library** — scanned pages as reusable assets
- **Reports** — run history, healing log, and activity
- **Settings** — configuration and migration controls

Start by scanning a page in **Library**, then create a scenario in **Scenarios** and click ▶ Run now.
""")
