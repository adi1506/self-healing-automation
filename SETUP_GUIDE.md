# Self-Healing Test Automation — VM Setup Guide

This guide walks through installing and running the app on a fresh client VM. Two paths are provided: **Linux** (recommended, matches our existing deployment) and **Windows**. Pick one.

---

## Part 1 — What to ask the client to provision

Send the client this checklist before you start.

### 1.1 VM specs

| Item | Minimum | Recommended | Notes |
|------|---------|-------------|-------|
| OS | Ubuntu 22.04 / Amazon Linux 2023 / Windows Server 2019+ | Amazon Linux 2023 or Ubuntu 22.04 | Linux is what our other deployments run on |
| vCPU | 4 cores | 8+ cores | Ollama LLM inference is CPU-bound when no GPU |
| RAM | 16 GB | 32 GB+ | Phi-4 14B model needs ~10 GB resident; smaller models work on 16 GB |
| Disk | 30 GB free | 60 GB+ | Models alone are 5–10 GB each; runs/screenshots accumulate |
| GPU | not required | optional NVIDIA (CUDA) | Speeds up Ollama significantly but app works CPU-only |
| Network | outbound HTTPS to the internet during install | same + access to target test sites | Only needed during install (pip + Playwright + Ollama model pull). After install the app can run fully offline. |

### 1.2 Software to be pre-installed (or admin rights to install)

Ask the client to either pre-install these **or** grant the deploy account `sudo`/Administrator rights so we can install them:

**Required**

1. **Python 3.10 or 3.11** (3.12 also works) — with `pip` and `venv`.
2. **Git** — for pulling the repo (or alternatively the client lets us upload a zip).
3. **Ollama** — local LLM server. Installer from <https://ollama.com>. We'll pull the model after install.
4. **Playwright system dependencies** — Playwright bundles its own Chromium, but it needs OS shared libraries (fonts, libnss, libxkbcommon, etc.). On Linux these are installed with `playwright install-deps` (needs sudo).

**Required only for Linux servers running in headed/visible-browser mode**

5. **Xvfb** (`xvfb` package) — virtual display for running Chromium with a visible UI on a headless server. Not needed if we run fully headless (default).

**Optional**

6. **tmux** or **systemd** access — to keep Streamlit and Ollama running after the SSH session ends.
7. **A reverse proxy** (nginx, Caddy, or an ALB) — only if the app should be reachable on port 80/443 with a domain. Streamlit's default port is **8501**.
8. **TLS certificate** — only if exposing externally over HTTPS.

### 1.3 Network / firewall rules

- **Inbound:** open port **8501** (or whatever port you proxy through). Restrict to client's VPN / office IP range if possible — the app currently has no built-in authentication.
- **Outbound during install:** HTTPS to `pypi.org`, `playwright.azureedge.net` / `cdn.playwright.dev`, `ollama.com`, `huggingface.co`, `registry.ollama.ai`.
- **Outbound at runtime:** HTTPS to whatever test sites the QA team plans to automate. If those sites sit behind a corporate WAF / IP allow-list, the VM's egress IP must be allow-listed.

### 1.4 Accounts and access

- A non-root user (e.g. `appuser` or the equivalent of `ec2-user`) we deploy under.
- SSH key access for our team (Linux) or RDP credentials (Windows).
- Decide on the install path. Our convention is `/data/projects/self-healing/latest/self-healing-automation-final` on Linux; any path is fine but it should be on a disk with ≥30 GB free.

### 1.5 Credentials / secrets

The app itself **does not require any API keys** in its default configuration — all AI calls go to the local Ollama server. The client only needs to provide:

- Credentials for any target test sites the QA team intends to automate (these get entered into Scenarios at runtime, not into a config file).

---

## Part 2 — Step-by-step installation (Linux: Ubuntu 22.04 / Amazon Linux 2023)

All commands assume you're SSHed in as the deploy user (e.g. `ec2-user` or `ubuntu`). Replace `<INSTALL_DIR>` with the chosen path, e.g. `/data/projects/self-healing/latest/self-healing-automation-final`.

### Step 1 — Install system packages

**Ubuntu / Debian**
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git xvfb tmux
```

**Amazon Linux 2023 / RHEL / Fedora**
```bash
sudo dnf install -y python3 python3-pip git tmux
# Xvfb (only needed for headed browser runs on a headless server)
sudo dnf install -y xorg-x11-server-Xvfb
```

Verify:
```bash
python3 --version    # expect 3.10, 3.11, or 3.12
git --version
```

### Step 2 — Install Ollama (local LLM server)

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Start the server (it usually starts automatically as a systemd unit; if not, run it under tmux):

```bash
# Option A — systemd (if the installer registered the service)
sudo systemctl enable --now ollama
systemctl status ollama

# Option B — manual under tmux
tmux new -d -s ollama 'ollama serve'
```

Pull the default model:
```bash
ollama pull phi4:14b
```

If 14B is too heavy for the VM, pull a smaller alternative instead and we'll switch in the app's Settings page:
```bash
ollama pull granite4:8b     # fastest, ~5 GB
# or
ollama pull mistral:7b      # smallest
```

Sanity check:
```bash
curl http://localhost:11434/api/tags    # should return JSON listing pulled models
```

### Step 3 — Get the project code

```bash
sudo mkdir -p /data/projects/self-healing/latest
sudo chown $USER:$USER /data/projects/self-healing/latest
cd /data/projects/self-healing/latest

# Option A — git clone (if the repo is reachable from the VM)
git clone <your-repo-url> self-healing-automation-final
cd self-healing-automation-final

# Option B — upload a zip via scp from your laptop, then unzip
# (run from your laptop):  scp self-healing.zip user@vm:/data/projects/self-healing/latest/
unzip self-healing.zip && cd self-healing-automation-final
```

### Step 4 — Create the Python virtual environment

```bash
cd <INSTALL_DIR>
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 5 — Install Playwright + Chromium

```bash
# still inside the activated .venv
playwright install chromium
sudo $(which playwright) install-deps chromium    # installs system libs Chromium needs
```

`install-deps` needs sudo because it apt/dnf-installs shared libraries. If the client refuses sudo, ask them to install the dependency list Playwright prints manually.

### Step 6 — Create runtime data folders

```bash
mkdir -p data/scans data/scenarios data/recipes data/flows screenshots
```

The default `data/settings.yaml` already points at `http://localhost:11434` with `mistral:latest`. After step 7 you can change the model from the Settings page in the UI — no file edits needed.

### Step 7 — First-run smoke test

```bash
source .venv/bin/activate
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

From your browser, hit `http://<vm-ip>:8501`. You should see the Streamlit landing page. Open **Settings** in the sidebar and confirm Ollama shows as connected and the model dropdown lists what you pulled.

If everything looks good, stop with `Ctrl+C` and move on to step 8 to make it persistent.

### Step 8 — Run persistently (pick one)

**Option A — tmux (simplest, matches existing deployment style)**
```bash
cd <INSTALL_DIR>
tmux new -d -s streamlit \
  'source .venv/bin/activate && streamlit run app.py --server.address 0.0.0.0 --server.port 8501'

# Reattach later:
tmux attach -t streamlit
# Detach without killing:  Ctrl-b then d
```

**Option B — systemd service (survives reboots cleanly)**

Create `/etc/systemd/system/self-healing.service`:
```ini
[Unit]
Description=Self-Healing Test Automation (Streamlit)
After=network-online.target ollama.service
Wants=ollama.service

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/data/projects/self-healing/latest/self-healing-automation-final
ExecStart=/data/projects/self-healing/latest/self-healing-automation-final/.venv/bin/streamlit run app.py --server.address 0.0.0.0 --server.port 8501
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now self-healing
sudo systemctl status self-healing
journalctl -u self-healing -f      # tail logs
```

### Step 9 — (Optional) Headed browser runs on a headless server

By default the app runs Chromium headless. For visible-browser debugging on a Linux server with no display, prefix the launch with `xvfb-run`:

```bash
SCANNER_HEADLESS=false xvfb-run -a streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

Available environment overrides (set in the service file or before `streamlit run`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_HOST` | `http://localhost:11434` | Point at a remote Ollama instance |
| `OLLAMA_MODEL` | `phi4:14b` | Override the model (also settable in UI) |
| `SCANNER_HEADLESS` | `true` | Set `false` for headed runs |
| `SCANNER_USER_AGENT` | spoofed Chrome 135 | Override UA for WAF testing |
| `SCANNER_LOCALE` | `en-IN` | Browser locale |
| `SCANNER_TIMEZONE` | `Asia/Kolkata` | Browser timezone |

---

## Part 3 — Step-by-step installation (Windows Server / Windows 11)

### Step 1 — Install prerequisites

Download and run the installers (or use `winget` from an elevated PowerShell):
```powershell
winget install --id Python.Python.3.11 -e
winget install --id Git.Git -e
winget install --id Ollama.Ollama -e
```

Open a new PowerShell window so the new PATH entries take effect.

### Step 2 — Start Ollama and pull a model

Ollama installs as a background service on Windows. Confirm and pull the model:
```powershell
ollama pull phi4:14b
curl http://localhost:11434/api/tags
```

### Step 3 — Get the code

```powershell
cd C:\apps        # or wherever you want it
git clone <your-repo-url> self-healing-automation
cd self-healing-automation
```

### Step 4 — Create the venv and install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

Playwright on Windows does not need `install-deps` — the required libraries are bundled.

### Step 5 — Run

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

Browse to `http://<vm-ip>:8501`. Open **Windows Defender Firewall** and allow inbound TCP on port 8501 (or whatever you proxy).

### Step 6 — Run as a service (optional)

Use **NSSM** (<https://nssm.cc>) to register Streamlit as a Windows service:
```powershell
nssm install SelfHealing "C:\apps\self-healing-automation\.venv\Scripts\streamlit.exe" `
  "run app.py --server.address 0.0.0.0 --server.port 8501"
nssm set SelfHealing AppDirectory "C:\apps\self-healing-automation"
nssm start SelfHealing
```

---

## Part 4 — Post-install verification

Once the app is running, walk through this checklist:

1. **Landing page loads** — `http://<vm-ip>:8501` shows the "Self-Healing Test Automation" title and sidebar.
2. **Settings page** — Ollama status shows green; model dropdown lists the model you pulled.
3. **Library page → Scan** — paste `https://the-internet.herokuapp.com/login` (or `test_form/sample_form.html` served locally), click Scan. An Excel element-map should appear.
4. **Scenarios page → New scenario** — pick the scanned page, add one step, click ▶ Run scenario. A run record appears in Reports with PASS.
5. **Tests pass** — from a shell with the venv active:
   ```bash
   pytest tests/ -v
   ```

If any of those fail, see the troubleshooting section.

---

## Part 5 — Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `playwright install chromium` succeeds but browser fails to launch | Missing system libs | `sudo $(which playwright) install-deps chromium` |
| Ollama tab in Settings shows "Not reachable" | Ollama service isn't running | `systemctl status ollama` or `tmux attach -t ollama` |
| Streamlit starts but is unreachable from outside the VM | Bound to localhost only, or firewall closed | Add `--server.address 0.0.0.0` and open inbound 8501 |
| "Headed mode requires DISPLAY" error | Linux headless box, no Xvfb | Wrap with `xvfb-run -a` or set `SCANNER_HEADLESS=true` |
| Phi-4 14B inference is unbearably slow | Box is CPU-only and under-provisioned | `ollama pull granite4:8b` and pick it in Settings |
| Target site blocks the scanner | Corporate WAF detects automation | Confirm VM egress IP is allow-listed; tweak `SCANNER_USER_AGENT` if needed |
| `pip install` fails on `playwright` | Python version mismatch | Confirm `python3 --version` is 3.10–3.12 |

---

## Part 6 — Updating the app later

```bash
cd <INSTALL_DIR>
git pull                       # or re-upload a fresh zip
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium    # re-run after any Playwright version bump

# Then restart:
sudo systemctl restart self-healing      # systemd
# or
tmux kill-session -t streamlit && tmux new -d -s streamlit '...'   # tmux
```

Runtime data (`data/scans`, `data/scenarios`, `data/recipes`, `data/flows`, `screenshots/`) is preserved across upgrades — never delete it during an update.
