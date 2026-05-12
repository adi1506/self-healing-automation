import streamlit as st
from core.ai_service import get_ai_service

st.set_page_config(page_title="Settings", layout="wide")
st.title("Settings")

svc = get_ai_service()

st.subheader("AI Model")

col_status, col_test = st.columns([3, 1])
with col_status:
    if svc.is_available():
        st.success(f"Connected to {svc.host}")
    else:
        st.error(f"Not reachable at {svc.host}")
        if svc.last_error:
            st.caption(f"Last error: {svc.last_error}")
with col_test:
    if st.button("Test connection"):
        svc.reload()
        st.rerun()

new_host = st.text_input("Ollama host", value=svc.host)

installed: list[str] = []
if svc.is_available():
    try:
        listing = svc.client.list()
        installed = [m.get("name") or m.get("model") for m in listing.get("models", [])]
        installed = [n for n in installed if n]
    except Exception as e:
        st.warning(f"Could not list models: {e}")

if installed:
    if svc.model in installed:
        ordered = [svc.model] + [m for m in installed if m != svc.model]
    else:
        ordered = installed
    selected = st.radio("Installed models", options=ordered,
                        index=0, key="model_selector")
else:
    st.info("No installed models found.")
    selected = svc.model

if installed and "phi4:14b" not in installed:
    st.warning("Recommended model `phi4:14b` is not installed. Run on the Ollama host:\n\n"
               "```\nollama pull phi4:14b\n```")

if st.button("Save selection"):
    svc.save_config(host=new_host, model=selected)
    st.success(f"Saved. Now using {selected} at {new_host}.")
    st.rerun()

st.subheader("Storage paths (read-only)")
st.code("data/scans/         — scanned pages + element maps\n"
        "data/scenarios/     — scenarios YAML\n"
        "data/recipes/       — legacy recipes (auto-migrated)\n"
        "data/flows/         — legacy flows (auto-migrated)\n"
        "screenshots/        — run screenshots\n"
        "data/settings.yaml  — AI host/model (this page writes here)",
        language="text")

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
