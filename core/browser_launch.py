"""Shared Playwright launcher with WAF / bot-detection mitigations.

The scanner, setter, and crawler all need to look like a real browser to
sites sitting behind WAFs (Apptrana, Cloudflare, Akamai, etc). A plain
`chromium.launch(headless=True)` advertises automation in several ways
those WAFs check: `navigator.webdriver=true`, missing plugins, empty
`window.chrome`, the default headless UA, etc. Routing all browser
launches through this helper keeps the masking consistent.

Env-var overrides (set on the EC2 box, no code change needed to tune):
  SCANNER_HEADLESS   - "false" / "0" runs headed (use with xvfb-run on EC2)
  SCANNER_USER_AGENT - override the spoofed UA string
  SCANNER_LOCALE     - default "en-IN"
  SCANNER_TIMEZONE   - default "Asia/Kolkata"
"""
from __future__ import annotations

import os


_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)

# Patches the fingerprint surfaces that headless Chromium leaks. Runs in
# every new document before site scripts execute, so detection libraries
# (including Apptrana's client probe) see human-looking values.
_STEALTH_INIT_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-IN', 'en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        { name: 'PDF Viewer' },
        { name: 'Chrome PDF Viewer' },
        { name: 'Chromium PDF Viewer' },
        { name: 'Microsoft Edge PDF Viewer' },
        { name: 'WebKit built-in PDF' },
    ],
});
window.chrome = window.chrome || { runtime: {}, app: {}, csi: () => {}, loadTimes: () => {} };
const _origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (_origQuery) {
    window.navigator.permissions.query = (params) =>
        params && params.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : _origQuery(params);
}
// WebGL vendor/renderer — headless reports SwiftShader, which is a tell.
try {
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(p) {
        if (p === 37445) return 'Intel Inc.';
        if (p === 37446) return 'Intel Iris OpenGL Engine';
        return getParameter.call(this, p);
    };
} catch (e) {}
"""


def _resolve_headless(explicit: bool | None) -> bool:
    """Resolve effective headless mode.

    Precedence: explicit arg > SCANNER_HEADLESS env > default True.
    The env var is the EC2 escape hatch: set SCANNER_HEADLESS=false and run
    streamlit under `xvfb-run -a` to drive a headed Chromium, which gets
    past WAFs that fingerprint headless mode itself.
    """
    if explicit is not None:
        return explicit
    raw = os.environ.get("SCANNER_HEADLESS")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


async def launch_browser_and_page(p, *, headless: bool | None = None):
    """Launch a chromium browser and return (browser, page) with stealth applied.

    Mirrors the previous `browser.new_page()` shape so call sites only swap
    two lines. The returned page already has the stealth init script and
    sits inside a context configured with a realistic UA, viewport, locale,
    timezone, and Accept-Language header.
    """
    effective_headless = _resolve_headless(headless)
    user_agent = os.environ.get("SCANNER_USER_AGENT", _DEFAULT_UA)
    locale = os.environ.get("SCANNER_LOCALE", "en-IN")
    timezone_id = os.environ.get("SCANNER_TIMEZONE", "Asia/Kolkata")

    # --no-sandbox: required when running as root in many EC2 / container setups.
    # --disable-blink-features=AutomationControlled: hides the CDP automation flag
    #   that WAFs key off (independent of the navigator.webdriver patch below —
    #   some detectors read the flag at the browser level before JS runs).
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    browser = await p.chromium.launch(headless=effective_headless, args=launch_args)
    context = await browser.new_context(
        user_agent=user_agent,
        viewport={"width": 1920, "height": 1080},
        locale=locale,
        timezone_id=timezone_id,
        ignore_https_errors=True,
        extra_http_headers={
            "Accept-Language": f"{locale},en;q=0.9",
        },
    )
    await context.add_init_script(_STEALTH_INIT_JS)
    page = await context.new_page()
    return browser, page
