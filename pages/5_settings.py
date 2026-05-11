import os
import streamlit as st

st.set_page_config(page_title="Settings", layout="wide")
st.title("Settings")

st.subheader("Ollama")
st.text_input("OLLAMA_HOST", value=os.environ.get("OLLAMA_HOST", ""), disabled=True,
              help="Set via environment variable before launching Streamlit.")
st.text_input("OLLAMA_MODEL", value=os.environ.get("OLLAMA_MODEL", "mistral"), disabled=True)

st.subheader("Storage paths (read-only)")
st.code("data/scans/         — scanned pages + element maps\n"
        "data/scenarios/     — scenarios YAML\n"
        "data/recipes/       — legacy recipes (auto-migrated)\n"
        "data/flows/         — legacy flows (auto-migrated)\n"
        "screenshots/        — run screenshots", language="text")

st.subheader("Re-run migration")
if st.button("Migrate legacy data now"):
    from core.scenario_migration import migrate_all
    report = migrate_all(
        recipes_dir="data/recipes",
        flows_dir="data/flows",
        scans_dir="data/scans",
        scenarios_dir="data/scenarios",
    )
    st.success(f"Migration ran: {report}")
