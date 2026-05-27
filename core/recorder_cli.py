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
from typing import Optional, Tuple

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


async def _required_fields_from_page(page) -> list[dict]:
    """Snapshot required fields on the page using inject.js's helper.
    Returns [] if the helper isn't available (e.g., page not yet injected)."""
    try:
        return await page.evaluate(
            "() => (window.__sha && window.__sha.scanRequiredFields) ? window.__sha.scanRequiredFields() : []"
        )
    except Exception:
        return []


async def _all_fields_from_page(page) -> list[dict]:
    """Snapshot every interactive field on the page via scanAll().

    Used to seed Recording.record_time_fields so replay-time schema diff can
    distinguish "field added after recording" from "field present at recording
    but never filled." Returns [] if the injected scanner isn't available.
    """
    try:
        return await page.evaluate(
            "() => (window.__sha && window.__sha.scanAll) ? window.__sha.scanAll() : []"
        )
    except Exception:
        return []


async def _snapshot_page(session) -> Optional[Tuple[str, list[dict], list[dict], list[dict]]]:
    """Capture (url, candidates, required_fields, all_fields) from the live page.

    Returns None when the page is closed or capture fails partway — callers
    keep their previous snapshot in that case. URL, candidates, required, and
    all-fields scans are returned together so the picker never shows
    a URL paired with candidates from a different page.
    """
    if session.page.is_closed():
        return None
    try:
        url = session.page.url
        candidates = await _candidates_from_page(session.page)
        required = await _required_fields_from_page(session.page)
        all_fields = await _all_fields_from_page(session.page)
        return url, candidates, required, all_fields
    except Exception:
        return None


async def _snapshot_storage(session) -> Optional[dict]:
    """Capture context.storage_state() if the context is still live."""
    if session._context is None:
        return None
    try:
        return await session._context.storage_state()
    except Exception:
        return None


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

    # Snapshots are refreshed during the recording, so the latest post-login
    # state is already in memory by the time the user closes the window.
    # Reading page.url / page.evaluate AFTER close always fails, which is the
    # bug this guards against.
    final_url = ""
    candidates: list[dict] = []
    state_payload: Optional[dict] = None
    required_fields_per_page: dict[str, list[dict]] = {}
    all_fields_per_page: dict[str, list[dict]] = {}

    want_state = bool(args.output_storage_state)

    async def refresh() -> None:
        nonlocal final_url, candidates, state_payload
        snap = await _snapshot_page(session)
        if snap is not None:
            final_url, candidates, required, all_fields = snap
            required_fields_per_page[final_url] = required
            all_fields_per_page[final_url] = all_fields
        if want_state:
            state_snap = await _snapshot_storage(session)
            if state_snap is not None:
                state_payload = state_snap

    if args.auto_close_ms and args.auto_close_ms > 0:
        await session.page.wait_for_timeout(args.auto_close_ms)
        await refresh()
    else:
        last_seen_url: Optional[str] = None
        while not session.page.is_closed():
            try:
                cur_url = session.page.url
            except Exception:
                break
            if cur_url != last_seen_url:
                last_seen_url = cur_url
                try:
                    await session.page.wait_for_load_state("load", timeout=3000)
                except Exception:
                    pass
                await refresh()
            await asyncio.sleep(0.5)

    recording = await session.stop(name=args.name or "untitled")

    # Flatten the all-fields scans collected across every page the user
    # touched into Recording.record_time_fields. Each entry is a slim
    # fingerprint dict the replay schema-diff can match against by name
    # or label. Dedupe by (name, nearest_label_text) so a field visited on
    # multiple pages doesn't show up twice.
    slim_fields: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()
    for fields in all_fields_per_page.values():
        for fp in fields:
            attrs = fp.get("attributes") or {}
            name = (attrs.get("name") or "").strip()
            label = (attrs.get("nearest_label_text") or "").strip()
            key = (name.lower(), label.lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            slim_fields.append({
                "id": fp.get("id", ""),
                "name": name,
                "nearest_label_text": label,
                "autocomplete": attrs.get("autocomplete", "") or "",
                "tag": (attrs.get("tag") or "").lower(),
                "is_required": bool(attrs.get("is_required", False)),
            })
    recording.record_time_fields = slim_fields

    Path(args.output_recording).parent.mkdir(parents=True, exist_ok=True)
    save_recording(args.output_recording, recording)

    Path(args.output_candidates).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_candidates, "w", encoding="utf-8") as f:
        json.dump({
            "candidates": candidates,
            "final_url": final_url,
            "required_fields_per_page": required_fields_per_page,
        }, f)

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
