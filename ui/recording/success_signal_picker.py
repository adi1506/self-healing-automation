"""Renders a checkbox list of candidate elements (captured by the
recorder CLI when the user closed the browser) and returns a
SuccessSignal when the user confirms."""
from __future__ import annotations
from datetime import datetime, timezone
import streamlit as st

from core.recording import ElementFingerprint, SuccessSignal


def render_picker(candidates: list[dict], url: str, key_prefix: str = "ss") -> SuccessSignal | None:
    st.markdown("**Confirm what proves you're logged in.** Pick one or more elements that should be visible on a logged-in page:")
    url_pattern = st.text_input(
        "URL contains (substring)",
        value=url.split("?")[0].split("#")[0],
        key=f"{key_prefix}_url",
    )
    picks: list[ElementFingerprint] = []
    for i, fp_dict in enumerate(candidates):
        fp = ElementFingerprint.from_dict(fp_dict)
        label_text = (
            fp.attributes.get("aria_label")
            or fp.attributes.get("text_content")
            or fp.attributes.get("nearest_label_text")
            or fp.primary_locator["value"]
        )[:80]
        if st.checkbox(f"Element: {label_text!r}", key=f"{key_prefix}_cand_{i}"):
            picks.append(fp)
    if st.button("Confirm signal", key=f"{key_prefix}_confirm"):
        return SuccessSignal(
            url_pattern=url_pattern,
            required_elements=picks,
            forbidden_elements=[],
            captured_at=datetime.now(timezone.utc).isoformat(),
        )
    return None
