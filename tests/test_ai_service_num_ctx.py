import time
from unittest.mock import patch
from core.ai_service import AIService, reset_ai_service


def test_generate_json_sets_num_ctx_to_model_max(tmp_path):
    reset_ai_service()
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc.model = "phi4:14b"
    svc._available = True
    svc._available_at = time.monotonic()
    with patch.object(svc.client, "generate") as mock_gen:
        mock_gen.return_value = {"response": '{"value": "x"}'}
        svc.generate_json("prompt")
    opts = mock_gen.call_args.kwargs["options"]
    assert opts["num_ctx"] == 16384
    assert opts["temperature"] == 0.0


def test_num_ctx_falls_back_for_unknown_model(tmp_path):
    reset_ai_service()
    svc = AIService(settings_path=str(tmp_path / "s.yaml"))
    svc.model = "some-unlisted-model:7b"
    assert svc._num_ctx() == 8192
