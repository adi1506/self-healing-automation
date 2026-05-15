import json
import os
import subprocess
import sys
from pathlib import Path
import pytest


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
