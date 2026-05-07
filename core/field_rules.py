from __future__ import annotations

import os
import re
import yaml


class FieldRulesStore:
    """Per-URL sidecar storage for plain-English per-field rules.

    Lives at: <data_dir>/<sanitized_url>.field_rules.yaml
    Schema:
        field_rules:
          email: "Always use Gmail addresses"
          city:  "Always Mumbai"
    """

    def __init__(self, data_dir: str = "data/scans"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

    def _sanitize_url(self, url: str) -> str:
        url_no_fragment = re.sub(r"#.*$", "", url)
        sanitized = re.sub(r"https?://", "", url_no_fragment)
        sanitized = re.sub(r"[^a-zA-Z0-9]", "_", sanitized)
        sanitized = re.sub(r"_+", "_", sanitized).strip("_")
        return sanitized

    def _path(self, url: str) -> str:
        return os.path.join(self.data_dir, f"{self._sanitize_url(url)}.field_rules.yaml")

    def read(self, url: str) -> dict[str, str]:
        path = self._path(url)
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        rules = data.get("field_rules") or {}
        return {str(k): str(v) for k, v in rules.items() if v}

    def save(self, url: str, rules: dict[str, str]) -> None:
        path = self._path(url)
        cleaned = {str(k): str(v) for k, v in (rules or {}).items() if v}
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"field_rules": cleaned}, f, sort_keys=True, allow_unicode=True)
