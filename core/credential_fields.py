"""Heuristic detection of identity/credential fields (username, email-as-login,
password). Used only to power a non-silent 'lock likely credentials' helper in
the recording test-case generator — it suggests, it never acts on its own."""
from __future__ import annotations

import re

_CRED_RE = re.compile(
    r"\b(user(name)?|user[_-]?id|userid|e-?mail|login|pass(word)?|pwd|passcode)\b",
    re.IGNORECASE,
)


def looks_like_credential(attrs: dict) -> bool:
    """True when an element's captured attributes look like a username / email /
    password field. Works for HTML inputs and Flutter <flt-semantics> nodes
    (which expose aria_label / text_content instead of type / name)."""
    if (attrs.get("type") or "").strip().lower() == "password":
        return True
    ac = (attrs.get("autocomplete") or "").strip().lower()
    if ac in ("username", "current-password", "new-password"):
        return True
    haystack = " ".join(
        str(attrs.get(k, "") or "")
        for k in ("aria_label", "nearest_label_text", "name", "id",
                  "placeholder", "text_content")
    )
    return bool(_CRED_RE.search(haystack))
