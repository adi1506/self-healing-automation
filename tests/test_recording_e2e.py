import os
import pytest
from core.recorder import RecorderSession
from core.recording import save_recording, load_recording
from core.replay import replay_recording


@pytest.fixture
def sample_form_url():
    return "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")


@pytest.mark.asyncio
async def test_record_then_replay_roundtrip(sample_form_url, tmp_path):
    # ---- record ---------------------------------------------------
    session = RecorderSession(application_id="app-e2e", headless=True)
    await session.start(start_url=sample_form_url)
    # Drive the form (in real use the user does this in the headed browser).
    first_input_name = await session.page.evaluate(
        "() => document.querySelector('input').getAttribute('name')"
    )
    if not first_input_name:
        pytest.skip("sample form first input lacks a name")
    await session.page.fill(f"[name='{first_input_name}']", "E2E-VALUE")
    await session.page.evaluate(
        f"() => document.querySelector(\"[name='{first_input_name}']\").dispatchEvent(new Event('change', {{bubbles:true}}))"
    )
    await session.page.wait_for_timeout(100)
    recording = await session.stop(name="e2e")
    assert any(s.action == "fill" and s.value == "E2E-VALUE" for s in recording.steps)

    # ---- persist + reload -----------------------------------------
    path = tmp_path / "rec.yaml"
    save_recording(str(path), recording)
    reloaded = load_recording(str(path))
    assert reloaded == recording

    # ---- replay ---------------------------------------------------
    outcome = await replay_recording(reloaded, headless=True)
    assert outcome.failed_step_index is None, f"replay failed: {outcome.error}"
    assert outcome.completed_steps == len(recording.steps)
