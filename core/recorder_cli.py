"""CLI entry point for a recording session.

Invoked as a subprocess from the Streamlit UI. Owns its own asyncio loop
so it stays orthogonal to Streamlit's rerun cycle. Records until either:
  - the user closes the Chromium window (production use), or
  - --auto-close-ms elapses (test mode).

On stop, writes:
  - <output-recording>:  the Recording YAML
  - <output-candidates>: a JSON blob with the success-signal candidates
                         (top-N distinctive elements on the page at stop time)
                         and the final URL.
  - <output-storage-state> (optional): the Playwright context.storage_state()
                         as JSON (NOT encrypted — the Streamlit page
                         encrypts before persisting).
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from pathlib import Path

from core.recorder import RecorderSession
from core.recording import save_recording


async def _candidates_from_page(page) -> list[dict]:
    return await page.evaluate(
        """(maxN) => {
            const seen = new Set();
            const out = [];
            const all = document.querySelectorAll('a, button, [role=button], [data-testid], [aria-label], input, select');
            for (const el of all) {
                const rect = el.getBoundingClientRect();
                if (rect.width < 10 || rect.height < 10) continue;
                if (!window.__sha) continue;
                const fp = window.__sha.buildFingerprint(el);
                if (seen.has(fp.id)) continue;
                seen.add(fp.id);
                const score =
                    (fp.attributes.id ? 4 : 0) +
                    (fp.attributes.aria_label ? 3 : 0) +
                    ((fp.attributes.text_content || '').length > 0 ? 1 : 0);
                out.push({fp, score});
                if (out.length >= 50) break;
            }
            out.sort((a,b) => b.score - a.score);
            return out.slice(0, maxN).map(c => c.fp);
        }""",
        5,
    )


async def _run(args: argparse.Namespace) -> int:
    storage_state = None
    if args.storage_state_path:
        p = Path(args.storage_state_path)
        if p.exists():
            storage_state = json.loads(p.read_text(encoding="utf-8"))

    session = RecorderSession(
        application_id=args.app_id,
        headless=(args.headless.lower() == "true"),
        storage_state=storage_state,
    )
    await session.start(start_url=args.start_url)

    if args.auto_close_ms and args.auto_close_ms > 0:
        await session.page.wait_for_timeout(args.auto_close_ms)
    else:
        # Wait until the user closes the window.
        while not session.page.is_closed():
            await asyncio.sleep(0.5)

    # Capture candidates BEFORE stopping (stop closes the page).
    candidates: list[dict] = []
    final_url = ""
    state_payload = None
    try:
        if not session.page.is_closed():
            final_url = session.page.url
            candidates = await _candidates_from_page(session.page)
            if args.output_storage_state and session._context:
                try:
                    state_payload = await session._context.storage_state()
                except Exception:
                    state_payload = None
    except Exception:
        pass  # page closed unexpectedly; candidates/state stay empty

    recording = await session.stop(name=args.name or "untitled")

    Path(args.output_recording).parent.mkdir(parents=True, exist_ok=True)
    save_recording(args.output_recording, recording)

    Path(args.output_candidates).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_candidates, "w", encoding="utf-8") as f:
        json.dump({"candidates": candidates, "final_url": final_url}, f)

    if state_payload is not None and args.output_storage_state:
        Path(args.output_storage_state).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_storage_state, "w", encoding="utf-8") as f:
            json.dump(state_payload, f)

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scenario recording CLI")
    ap.add_argument("--app-id", required=True)
    ap.add_argument("--start-url", required=True)
    ap.add_argument("--output-recording", required=True)
    ap.add_argument("--output-candidates", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--storage-state-path", default="")
    ap.add_argument("--output-storage-state", default="")
    ap.add_argument("--headless", default="false", help="'true' or 'false'")
    ap.add_argument("--auto-close-ms", type=int, default=0,
                    help="auto-stop after N ms (test mode); 0 = wait for user to close window")
    args = ap.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
