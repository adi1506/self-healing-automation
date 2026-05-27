import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

from core.recorder import RecorderSession
from core.recorder_cli import _snapshot_page, _snapshot_storage


@pytest.mark.skipif(os.environ.get("CI") == "true", reason="needs interactive close — skipped in CI")
def test_recorder_cli_smoke_run(tmp_path):
    """Smoke test: launch the CLI, auto-close after a fixed time, verify the
    output file is written.

    To keep the test automatable, we use --auto-close-ms=N to make the CLI
    auto-stop after N milliseconds of recording.
    """
    sample = "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")
    out_recording = tmp_path / "rec.yaml"
    out_candidates = tmp_path / "candidates.json"
    proc = subprocess.run(
        [
            sys.executable, "-m", "core.recorder_cli",
            "--app-id", "test-app",
            "--start-url", sample,
            "--output-recording", str(out_recording),
            "--output-candidates", str(out_candidates),
            "--auto-close-ms", "1500",
            "--headless", "true",
        ],
        capture_output=True, timeout=60, text=True,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    assert out_recording.exists()
    assert out_candidates.exists()
    candidates_data = json.loads(out_candidates.read_text(encoding="utf-8"))
    assert "candidates" in candidates_data
    assert "final_url" in candidates_data


@pytest.mark.asyncio
async def test_snapshot_survives_post_navigation_close():
    """Regression: the picker was being pre-filled with the START url because
    the CLI captured state AFTER the page was already closed. The fix snapshots
    on every URL change during the recording, so the latest pre-close state is
    retained even though `page.url` is unreadable after close.
    """
    start = "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")
    target = "file://" + os.path.abspath("test_form/v2_id_changes.html").replace("\\", "/")
    start_marker = "sample_form.html"
    target_marker = "v2_id_changes.html"

    session = RecorderSession(application_id="snapshot-test", headless=True)
    await session.start(start_url=start)

    final_url = ""
    candidates: list[dict] = []

    snap = await _snapshot_page(session)
    assert snap is not None
    final_url, candidates, _required, _all_fields = snap
    assert start_marker in final_url
    first_candidates = list(candidates)

    await session.page.goto(target)
    await session.page.wait_for_load_state("load")

    snap = await _snapshot_page(session)
    assert snap is not None
    final_url, candidates, _required, _all_fields = snap
    assert target_marker in final_url

    await session.page.close()

    snap = await _snapshot_page(session)
    assert snap is None, "snapshot must refuse to read from a closed page"

    # The variables retained from the last successful snapshot — this is what
    # the CLI writes out as final_url / candidates.
    assert target_marker in final_url
    assert candidates  # non-empty

    try:
        await session.stop(name="snapshot-test")
    except Exception:
        pass
