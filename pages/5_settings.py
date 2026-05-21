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
if svc.client is not None:
    try:
        listing = svc.client.list()
        installed = svc._extract_model_names(listing)
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

st.subheader("Diagnose model")
st.caption(
    "Runs a real `generate` call against the configured model and shows the raw "
    "response. Use this to confirm the model actually produces parseable JSON on "
    "this host (e.g. on EC2)."
)
if st.button("Run end-to-end test"):
    import time as _t
    if svc.client is None:
        st.error("Ollama SDK is not installed in this environment.")
    else:
        prompt = (
            "Return ONLY valid JSON with one key, exactly: "
            '{"ok": true}. No prose, no code fence, no <think> tags.'
        )
        st.write(f"**Host:** `{svc.host}`  |  **Model:** `{svc.model}`")
        try:
            t0 = _t.monotonic()
            response = svc.client.generate(
                model=svc.model, prompt=prompt,
                format="json", options={"temperature": 0.0},
            )
            elapsed_ms = (_t.monotonic() - t0) * 1000.0
            raw_text = svc._extract_response_text(response)
            parsed = svc._parse_json_response(raw_text) if raw_text else None
            st.write(f"**Response type:** `{type(response).__name__}`  |  "
                     f"**Latency:** {elapsed_ms:.0f} ms  |  "
                     f"**Raw length:** {len(raw_text)} chars")
            st.markdown("**Raw model output:**")
            st.code(raw_text or "(empty)", language="text")
            if parsed is not None:
                st.success(f"Parsed JSON: `{parsed}` — model is working end-to-end.")
            else:
                st.error(
                    "Could not parse the response as JSON. The model is reachable but "
                    "its output is not consumable by this app. Try a different model "
                    "(e.g. `qwen2.5:3b`) or check the raw output above."
                )
        except Exception as e:
            st.error(f"Generate call failed: {type(e).__name__}: {e}")

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
