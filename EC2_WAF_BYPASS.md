# Running the scanner against WAF-protected sites on EC2

## The problem

The HDBFS client URL (`https://mcoput.hdbfs.com:8017/webapp/#/internal/login`)
sits behind **Radware Bot Manager**, a JavaScript-based bot-detection wall.
Plain `curl` from EC2 returns **406 Not Acceptable**, and a vanilla Playwright
headless browser gets blocked too. We could not whitelist the EC2 IP from the
client side, so we had to make the EC2 browser look like a real desktop browser.

Two things make a browser detectable as a bot:

1. **Fingerprint** — properties like `navigator.webdriver=true`, missing WebGL
   drivers, default headless user-agent, missing browser plugins.
2. **Headless mode itself** — most bot walls explicitly check whether Chromium
   was launched with `--headless`.

We solved both. The code was already wired for this scenario; only the EC2 box
needed configuration.

## The solution in one line

> Run a **real headed Chromium** on EC2 by giving it a fake display (Xvfb), and
> use the fingerprint patches already in `core/browser_launch.py`.

Plus a small scanner fix so SPAs that render their form late (HDBFS does) get
enough time to finish rendering before the scanner reads the DOM.

## What was already in the codebase (no changes needed)

`core/browser_launch.py` was already set up for this:

- Patches `navigator.webdriver`, `navigator.plugins`, `navigator.languages`,
  `window.chrome`, and WebGL vendor/renderer so the page sees real-Chrome values.
- Reads `SCANNER_HEADLESS=false` env var to switch from headless to headed mode.
- Launches Chromium with `--disable-blink-features=AutomationControlled` (hides
  the CDP automation flag that bot walls read).
- Spoofs UA, locale (en-IN), timezone (Asia/Kolkata), and viewport.

Everything else (scanner, setter, crawler, scenario replay) calls
`launch_browser_and_page`, so they all benefit automatically.

## What we did on the EC2 box

### 1. Confirmed the WAF identity

```bash
curl -sI -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36" \
  -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" \
  -H "Accept-Language: en-IN,en;q=0.9" \
  https://mcoput.hdbfs.com:8017/webapp/
```

Response headers contained `X-MP-XAE2`, `X-RCOR`, and a `sess_map` cookie —
fingerprints of Radware Bot Manager.

### 2. Installed system libraries Chromium needs in headed mode

Amazon Linux 2023 doesn't ship the GUI libs by default. Run as root (the libs
are system-wide, not Python packages, so venv doesn't matter):

```bash
sudo dnf install -y nss atk at-spi2-atk cups-libs libdrm libxkbcommon \
  mesa-libgbm alsa-lib libXcomposite libXdamage libXrandr libXScrnSaver \
  libXtst pango cairo gdk-pixbuf2 liberation-fonts google-noto-sans-fonts
```

Most were already present; only the fonts actually got added.

`Xvfb` and `xvfb-run` were already installed on this box at `/usr/bin/`.

### 3. Verified the stealth patches work end-to-end

```bash
cd /data/projects/self-healing/latest/self-healing-automation-final
source .venv/bin/activate
SCANNER_HEADLESS=false xvfb-run -a --server-args="-screen 0 1920x1080x24" python -c "
import asyncio
from playwright.async_api import async_playwright
from core.browser_launch import launch_browser_and_page
async def main():
    async with async_playwright() as p:
        b, page = await launch_browser_and_page(p)
        await page.goto('https://bot.sannysoft.com', wait_until='networkidle')
        await page.screenshot(path='/tmp/stealth-check.png', full_page=True)
        await b.close()
asyncio.run(main())
"
```

Downloaded `/tmp/stealth-check.png` via WinSCP and confirmed the important
rows (Chrome, Permissions, Plugins, WebGL Vendor, WebGL Renderer, Languages,
User Agent) were all green.

### 4. Verified the real client URL works

```bash
SCANNER_HEADLESS=false xvfb-run -a python -c "
import asyncio
from playwright.async_api import async_playwright
from core.browser_launch import launch_browser_and_page
async def main():
    async with async_playwright() as p:
        b, page = await launch_browser_and_page(p)
        resp = await page.goto('https://mcoput.hdbfs.com:8017/webapp/#/internal/login', wait_until='networkidle', timeout=60000)
        await page.wait_for_timeout(5000)
        print('STATUS:', resp.status)
        print('TITLE:', await page.title())
        print('HAS_INPUTS:', await page.locator('input').count())
        await b.close()
asyncio.run(main())
"
```

Got `STATUS: 200`, `TITLE: HDB Finance`, `HAS_INPUTS: 2`. The WAF was bypassed.

### 5. Fixed a late-render bug in the scanner

When running through the Streamlit UI, the scanner found 0 elements even though
the WAF was passing. The HDBFS SPA renders the login fields ~5 seconds *after*
`networkidle` fires (a follow-up bootstrap request that doesn't reopen the idle
clock). The scanner was reading the DOM before the fields existed.

Added a helper to `core/scanner.py` that polls for form fields (inputs,
textareas, selects) after `networkidle`, with a 12-second deadline. Static
pages return on the first poll (no overhead). Slow SPAs get the time they
need.

The patch added one helper function and one call site in `_scan_async`.

### 6. Launched the app under Xvfb

The two things that matter:

- `DISPLAY=:99` — tells Chromium which X server to talk to.
- `SCANNER_HEADLESS=false` — switches `core/browser_launch.py` from headless to
  headed mode.

Started both as detached tmux sessions to match the existing operational style
on this box:

```bash
tmux new -d -s xvfb "Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset"
tmux new -d -s selfheal "cd /data/projects/self-healing/latest/self-healing-automation-final && source .venv/bin/activate && DISPLAY=:99 SCANNER_HEADLESS=false streamlit run app.py"
```

Streamlit auto-picks a port (`8501`, `8502`, etc.). Find the chosen port with:

```bash
tmux capture-pane -t selfheal -p | grep -i URL
```

Open `http://<EC2-public-IP>:<port>` in a browser. The public IP came from:

```bash
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4
```

Make sure the AWS Security Group has an inbound rule for whatever port
Streamlit picked.

## What the Xvfb command actually does

```bash
tmux new -d -s xvfb "Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset"
```

- `tmux new -d -s xvfb` — start a detached tmux session named `xvfb` so the
  process survives SSH disconnect.
- `Xvfb :99` — start the X Virtual Framebuffer on display number `:99`. Any
  process with `DISPLAY=:99` in its environment will render through this.
- `-screen 0 1920x1080x24` — one virtual screen at 1920×1080, 24-bit color.
- `-ac` — disable X access control (everything is local on this box, no need
  for auth).
- `+extension GLX` — enable OpenGL over X, so Chromium uses real WebGL instead
  of the SwiftShader fallback (which bot walls detect).
- `+render` — enable anti-aliased text and modern graphics.
- `-noreset` — don't reset when the last client disconnects (the scanner opens
  and closes Chromium many times; we want Xvfb to stay up).

## How to run the app on subsequent days

**Scenario A — EC2 is up, nothing crashed, you only need a fresh Streamlit
(after a code change):**

```bash
tmux kill-session -t selfheal 2>/dev/null
tmux new -d -s selfheal "cd /data/projects/self-healing/latest/self-healing-automation-final && source .venv/bin/activate && DISPLAY=:99 SCANNER_HEADLESS=false streamlit run app.py"
```

**Scenario B — EC2 was rebooted, everything is gone:**

```bash
tmux new -d -s xvfb "Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset"
tmux new -d -s selfheal "cd /data/projects/self-healing/latest/self-healing-automation-final && source .venv/bin/activate && DISPLAY=:99 SCANNER_HEADLESS=false streamlit run app.py"
```

**Scenario C — not sure what's running:**

```bash
tmux ls
```

- Both `xvfb` and `selfheal` present → already running, open the URL.
- Only `xvfb` present → run just the Streamlit command from Scenario A.
- Neither present → run both commands from Scenario B.

## Useful tmux commands

| Command | What it does |
|---|---|
| `tmux ls` | List all running sessions |
| `tmux attach -t selfheal` | Watch Streamlit logs live. Detach with `Ctrl+B` then `D`. Don't close the window. |
| `tmux capture-pane -t selfheal -p \| tail -30` | Print the latest output without attaching |
| `tmux kill-session -t selfheal` | Stop Streamlit |
| `tmux kill-session -t xvfb` | Stop Xvfb |

## Troubleshooting

**Streamlit hangs at the "Welcome to Streamlit — Email:" prompt on first run.**
Send a blank Enter to the session, then pre-create the credentials file so it
never asks again:

```bash
tmux send-keys -t selfheal "" Enter
mkdir -p ~/.streamlit
cat > ~/.streamlit/credentials.toml <<'EOF'
[general]
email = ""
EOF
```

**"This site can't be reached" when opening Streamlit in browser.** Two
causes:

1. You used the EC2's internal IP (`172.31.x.x`) instead of the public IP
   (`13.204.124.192`).
2. The Security Group doesn't have an inbound rule for the port Streamlit
   picked. Add one in **AWS Console → EC2 → Instances → Security tab →
   Security Group → Inbound rules**, type Custom TCP, port = whatever
   Streamlit picked, source = your IP or `0.0.0.0/0`.

**Scanner returns 0 elements but the page clearly has a form.** Diagnostic:

```bash
cd /data/projects/self-healing/latest/self-healing-automation-final
source .venv/bin/activate
SCANNER_HEADLESS=false xvfb-run -a python -c "
import asyncio
from playwright.async_api import async_playwright
from core.browser_launch import launch_browser_and_page
async def main():
    async with async_playwright() as p:
        b, page = await launch_browser_and_page(p)
        await page.goto('<the-url>', wait_until='networkidle', timeout=60000)
        await page.wait_for_timeout(7000)
        print('inputs:', await page.locator('input').count())
        print('buttons:', await page.locator('button').count())
        await b.close()
asyncio.run(main())
"
```

If this finds elements but `Scanner().scan(...)` doesn't, the form is rendering
even later than the 12-second deadline in `_wait_for_interactive`. Bump the
`deadline_seconds` argument in `core/scanner.py`.

**Real `curl` to the client URL still returns 406 from EC2.** That's expected.
Plain `curl` lacks the headers and JS execution Radware checks for. The
Streamlit app uses Chromium under Xvfb and goes through.

## Why this works against Radware specifically

Radware Bot Manager runs a JavaScript probe in the user's browser that checks:

- `navigator.webdriver` (must not be `true`)
- `window.chrome` (must exist and have realistic methods)
- WebGL vendor/renderer (must not be SwiftShader or Mesa/llvmpipe)
- Plugins array (real Chrome has at least the built-in PDF viewers)
- Whether the browser was launched with `--headless`
- Various timing fingerprints

`core/browser_launch.py` patches the first four. Xvfb solves the fifth by
letting us run with `headless=False` while still on a server with no monitor.
The timing fingerprints pass naturally because Chromium is doing real work,
not running in headless's optimised render path.

A residential proxy could be added on top if Radware ever escalates to IP-based
checks, but currently it's letting AWS IPs through if the browser passes the JS
probe, so we don't need that yet.
