from __future__ import annotations

import json
import os
import re
from datetime import datetime


class SiteManager:
    """Registry mapping a base URL to its discovered pages."""

    def __init__(self, data_dir: str = "data/sites"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

    def _sanitize(self, base_url: str) -> str:
        s = re.sub(r"https?://", "", base_url)
        s = re.sub(r"[^a-zA-Z0-9]", "_", s)
        return re.sub(r"_+", "_", s).strip("_")

    def _path(self, base_url: str) -> str:
        return os.path.join(self.data_dir, f"{self._sanitize(base_url)}.json")

    def register_site(self, base_url: str, page_urls: list[str]) -> None:
        manifest = {
            "base_url": base_url,
            "crawled_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "pages": list(page_urls),
        }
        with open(self._path(base_url), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

    def list_sites(self) -> list[str]:
        if not os.path.isdir(self.data_dir):
            return []
        sites = []
        for fname in os.listdir(self.data_dir):
            if not fname.endswith(".json"):
                continue
            with open(os.path.join(self.data_dir, fname), encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    continue
            if "base_url" in data:
                sites.append(data["base_url"])
        return sites

    def get_site_pages(self, base_url: str) -> list[str]:
        path = self._path(base_url)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("pages", []))

    def delete_site(self, base_url: str) -> None:
        path = self._path(base_url)
        if os.path.exists(path):
            os.remove(path)
