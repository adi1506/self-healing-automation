"""Capture engine — JS injection + helpers."""
import os

INJECT_JS_PATH = os.path.join(os.path.dirname(__file__), "inject.js")


def load_inject_js() -> str:
    with open(INJECT_JS_PATH, encoding="utf-8") as f:
        return f.read()
