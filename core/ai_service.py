"""Single owner of all Ollama interactions.

- Resolves host/model from data/settings.yaml > env vars > defaults.
- Caches is_available() for 30s.
- Exposes a singleton via get_ai_service().
"""
from __future__ import annotations

import os
import time
from pathlib import Path
import yaml

try:
    import ollama
except ImportError:
    ollama = None


DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "phi4:14b"
DEFAULT_SETTINGS_PATH = "data/settings.yaml"
AVAILABILITY_TTL_SEC = 30.0


class AIService:
    def __init__(self, settings_path: str = DEFAULT_SETTINGS_PATH):
        self.settings_path = settings_path
        self.host = DEFAULT_HOST
        self.model = DEFAULT_MODEL
        self._load_config()
        self.client = ollama.Client(host=self.host) if ollama is not None else None
        self._available = None
        self._available_at = 0.0
        self.last_error: str | None = None
        self.last_latency_ms: float | None = None

    # ---------------------------------------------------------------- config
    def _load_config(self) -> None:
        # Order: settings.yaml > env > default
        env_host = os.environ.get("OLLAMA_HOST", "").strip()
        env_model = os.environ.get("OLLAMA_MODEL", "").strip()
        if env_host:
            self.host = env_host
        if env_model:
            self.model = env_model

        path = Path(self.settings_path)
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                ai = data.get("ai") or {}
                if ai.get("host"):
                    self.host = str(ai["host"])
                if ai.get("model"):
                    self.model = str(ai["model"])
            except Exception as e:
                self.last_error = f"settings.yaml read failed: {e}"

    def save_config(self, *, host: str, model: str) -> None:
        """Persist host/model to settings.yaml and reload."""
        path = Path(self.settings_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"ai": {"host": host, "model": model}}
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        self.reload()

    def reload(self) -> None:
        """Re-read settings, reset client, clear availability cache."""
        self._load_config()
        self.client = ollama.Client(host=self.host) if ollama is not None else None
        self._available = None
        self._available_at = 0.0

    # ---------------------------------------------------------- availability
    def is_available(self) -> bool:
        if self.client is None:
            return False
        if self._available is not None and (time.monotonic() - self._available_at) < AVAILABILITY_TTL_SEC:
            return self._available
        try:
            self.client.list()
            self._available = True
            self.last_error = None
        except Exception as e:
            self._available = False
            self.last_error = str(e)
        self._available_at = time.monotonic()
        return self._available


# --------------------------------------------------------------------- singleton
_service: AIService | None = None


def get_ai_service(settings_path: str = DEFAULT_SETTINGS_PATH) -> AIService:
    global _service
    if _service is None:
        _service = AIService(settings_path=settings_path)
    return _service


def reset_ai_service() -> None:
    """Test helper — drop the singleton so a fresh one is built next call."""
    global _service
    _service = None
