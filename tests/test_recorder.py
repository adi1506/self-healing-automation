import os
import pytest
from core.recorder import RecorderSession


@pytest.fixture
def sample_form_url():
    return "file://" + os.path.abspath("test_form/sample_form.html").replace("\\", "/")


@pytest.mark.asyncio
async def test_recorder_captures_a_fill_event(sample_form_url):
    rec_session = RecorderSession(application_id="test-app", headless=True)
    await rec_session.start(start_url=sample_form_url)
    # Drive the page from the test (the user would do this manually in
    # real use); the recorder is paused on `start_url` waiting for events.
    await rec_session.page.fill("input", "hello world")
    await rec_session.page.evaluate(
        "() => document.querySelector('input').dispatchEvent(new Event('change', {bubbles:true}))"
    )
    await rec_session.page.wait_for_timeout(100)
    recording = await rec_session.stop(name="test recording")
    assert recording.application_id == "test-app"
    assert recording.start_url == sample_form_url
    assert recording.name == "test recording"
    assert any(s.action == "fill" and s.value == "hello world" for s in recording.steps)
    assert recording.steps[0].element is not None
    assert recording.steps[0].element.attributes["tag"] == "input"


@pytest.mark.asyncio
async def test_recorder_stops_cleanly_with_no_events(sample_form_url):
    rec_session = RecorderSession(application_id="empty-app", headless=True)
    await rec_session.start(start_url=sample_form_url)
    recording = await rec_session.stop(name="empty")
    assert recording.steps == []
