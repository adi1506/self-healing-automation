"""Manage applications + login recordings.

Recording is run in a subprocess. The user closes the browser window
to end the recording; this page polls for the output files.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import streamlit as st

from core.applications import (
    Application, save_application, list_applications, delete_application, load_application,
)
from core.auth_session import save_storage_state, is_storage_state_valid
from core.recording import load_recording, save_recording
from ui.recording.success_signal_picker import render_picker

APP_DIR = "data/applications"
STATE_DIR = "data/storage_states"
WORK_DIR = "data/recorder_work"

st.set_page_config(page_title="Recordings", page_icon="🎬")
st.title("Applications & Login Recordings")

# --- Applications list -------------------------------------------------
st.subheader("Applications")
apps = list_applications(APP_DIR)
for app in apps:
    cols = st.columns([3, 2, 2, 1])
    cols[0].write(f"**{app.name}** — `{app.base_url_pattern}`")
    cols[1].write("login ✓" if app.login_recording_id else "login ✗")
    health = "🟢" if is_storage_state_valid(app) else "🔴"
    cols[2].write(f"state {health}")
    if cols[3].button("Delete", key=f"del-{app.id}"):
        delete_application(APP_DIR, app.id)
        st.rerun()

st.divider()
st.subheader("New application")

with st.form("new_app"):
    name = st.text_input("Name")
    login_url = st.text_input("Login URL")
    submitted = st.form_submit_button("Create + record login")

if submitted and name and login_url:
    app = Application(
        id="app-" + uuid.uuid4().hex[:8],
        name=name,
        base_url_pattern=login_url,
    )
    save_application(APP_DIR, app)
    st.session_state["login_app_id"] = app.id
    st.session_state["login_url"] = login_url
    st.rerun()

# --- Login recording flow ---------------------------------------------
app_id = st.session_state.get("login_app_id")
if app_id:
    st.divider()
    st.subheader(f"Recording login for {app_id}")
    Path(WORK_DIR).mkdir(parents=True, exist_ok=True)
    rec_path = os.path.join(WORK_DIR, f"{app_id}_login.yaml")
    cand_path = os.path.join(WORK_DIR, f"{app_id}_candidates.json")
    state_path = os.path.join(WORK_DIR, f"{app_id}_state.json")

    if "login_proc_pid" not in st.session_state:
        st.info("Click Start to open a browser. Sign in normally. **Close the browser window** when you're on a logged-in page — that ends the recording.")
        if st.button("Start"):
            for p in (rec_path, cand_path, state_path):
                if os.path.exists(p):
                    os.remove(p)
            proc = subprocess.Popen(
                [
                    sys.executable, "-m", "core.recorder_cli",
                    "--app-id", app_id,
                    "--start-url", st.session_state["login_url"],
                    "--output-recording", rec_path,
                    "--output-candidates", cand_path,
                    "--output-storage-state", state_path,
                    "--name", f"login: {app_id}",
                    "--headless", "false",
                ]
            )
            st.session_state["login_proc_pid"] = proc.pid
            st.rerun()
    else:
        proc_done = (
            os.path.exists(rec_path) and os.path.exists(cand_path)
        )
        if not proc_done:
            st.warning("Recording in progress. Close the browser window when done, then click Refresh.")
            if st.button("Refresh"):
                st.rerun()
        else:
            cand_data = json.loads(Path(cand_path).read_text(encoding="utf-8"))
            signal = render_picker(
                cand_data["candidates"],
                cand_data["final_url"] or st.session_state["login_url"],
                key_prefix=f"ss_{app_id}",
            )
            if signal is not None:
                login_rec = load_recording(rec_path)
                login_rec.kind = "login"
                login_rec.success_signal = signal
                target = os.path.join(APP_DIR, app_id, "login_recording.yaml")
                save_recording(target, login_rec)

                app = load_application(APP_DIR, app_id)
                app.login_recording_id = login_rec.id

                # If the CLI captured storageState, encrypt and persist it.
                if os.path.exists(state_path):
                    payload = json.loads(Path(state_path).read_text(encoding="utf-8"))
                    save_storage_state(STATE_DIR, app_id, payload)
                    app.storage_state_path = os.path.join(STATE_DIR, app_id + ".enc")
                    os.remove(state_path)

                now = datetime.now(timezone.utc)
                app.storage_state_captured_at = now.isoformat()
                app.storage_state_expires_at = (now + timedelta(hours=12)).isoformat()
                save_application(APP_DIR, app)

                for k in ("login_proc_pid", "login_app_id", "login_url"):
                    st.session_state.pop(k, None)
                st.success(f"Login recorded for {app.name}.")
                st.rerun()
