"""Browser-level: capture engine must record clicks on Flutter widgets
(InkWell / GestureDetector tiles) even when the app didn't wrap them
in Semantics(button: true) — i.e. when the <flt-semantics> node has
no role attribute.

This is the recorder-side fix for the "missing tile click" failure
mode observed on Flutter web apps that use plain InkWell/Card-style
tiles for navigation. Without the fix, the click handler's
interactivity walk finds no recognized ancestor and silently drops
the event; the resulting recording skips the navigation step entirely
and replay lands on the wrong page at the next step.
"""
from __future__ import annotations

import urllib.parse

import pytest

from core.recorder import RecorderSession


def _flutter_like_page(tile_label: str = "Create Application") -> str:
    """Minimal page that mimics Flutter web's semantics overlay: a
    <flt-semantics-host> wrapping a tile-shaped <flt-semantics> with NO
    role attribute. Pinning attributes via id only for test addressing —
    the recorder fix must not depend on the id (real Flutter assigns
    flt-semantic-node-N ordinals)."""
    html = f"""<!doctype html>
<html><body style="margin:0">
<flt-semantics-host style="position:absolute;left:0;top:0;width:100%;height:100%;">
  <flt-semantics id="root" style="display:block;width:100vw;height:100vh;">
    <flt-semantics id="container" style="display:block;width:600px;height:400px;">
      <flt-semantics id="tile" style="display:block;width:200px;height:130px;
           position:absolute;left:50px;top:50px;background:#1976d2;color:white;
           cursor:pointer;">
        {tile_label}
      </flt-semantics>
    </flt-semantics>
  </flt-semantics>
</flt-semantics-host>
</body></html>"""
    return "data:text/html;charset=utf-8," + urllib.parse.quote(html)


@pytest.mark.asyncio
async def test_click_on_flutter_tile_without_role_is_captured():
    rec = RecorderSession(application_id="test-flutter", headless=True)
    await rec.start(start_url=_flutter_like_page())
    await rec.page.click("#tile")
    await rec.page.wait_for_timeout(150)
    raw_events = list(rec._events)
    await rec.stop(name="flutter-tile-click")

    click_events = [ev for ev in raw_events if ev.get("action") == "click"]
    assert len(click_events) >= 1, (
        f"expected at least one click event, got events: {[e.get('action') for e in raw_events]}"
    )
    # The captured target must be the tile, not the page-spanning root semantic
    # node — that root would be a useless click intent.
    matched_tile = False
    for ev in click_events:
        el = ev.get("element") or {}
        attrs = el.get("attributes") or {}
        if attrs.get("id") == "tile":
            matched_tile = True
            break
    assert matched_tile, (
        f"expected click captured on the tile element; instead got: "
        f"{[(ev.get('element') or {}).get('attributes', {}).get('id') for ev in click_events]}"
    )


@pytest.mark.asyncio
async def test_root_spanning_flutter_semantic_is_not_promoted_to_click_target():
    """If the user clicks empty space inside the viewport-spanning root
    semantic (no tile underneath), the fallback must NOT capture the
    root as a click target — that's not a meaningful user intent."""
    html = """<!doctype html>
<html><body style="margin:0">
<flt-semantics-host>
  <flt-semantics id="root" style="display:block;width:100vw;height:100vh;
       background:transparent;"></flt-semantics>
</flt-semantics-host>
</body></html>"""
    url = "data:text/html;charset=utf-8," + urllib.parse.quote(html)

    rec = RecorderSession(application_id="test-flutter-noop", headless=True)
    await rec.start(start_url=url)
    # Click into empty space inside the root semantic.
    await rec.page.mouse.click(50, 50)
    await rec.page.wait_for_timeout(150)
    raw_events = list(rec._events)
    await rec.stop(name="flutter-empty-click")

    click_events = [ev for ev in raw_events if ev.get("action") == "click"]
    # The only candidate is the viewport-spanning root — fallback rejects it.
    assert click_events == [], (
        f"root-spanning semantic should not be promoted to click target; got: "
        f"{[(ev.get('element') or {}).get('attributes', {}).get('id') for ev in click_events]}"
    )


@pytest.mark.asyncio
async def test_fallback_does_not_fire_on_non_flutter_pages():
    """On a normal HTML page with no <flt-semantics-host>, a click on a
    bare <div> (no role, no onclick) must still be dropped — the Flutter
    fallback must not change behavior for non-Flutter apps."""
    html = """<!doctype html>
<html><body><div id="bare" style="width:200px;height:50px">click me</div></body></html>"""
    url = "data:text/html;charset=utf-8," + urllib.parse.quote(html)

    rec = RecorderSession(application_id="test-non-flutter", headless=True)
    await rec.start(start_url=url)
    await rec.page.click("#bare")
    await rec.page.wait_for_timeout(150)
    raw_events = list(rec._events)
    await rec.stop(name="non-flutter-bare-div")

    click_events = [ev for ev in raw_events if ev.get("action") == "click"]
    assert click_events == [], (
        f"non-Flutter bare <div> click should remain dropped; got: "
        f"{[(ev.get('element') or {}).get('attributes', {}).get('id') for ev in click_events]}"
    )
