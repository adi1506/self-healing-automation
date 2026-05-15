from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class ElementFingerprint:
    """A multi-attribute fingerprint for one recorded element.

    The `id` is stable across every Step in the Recording that touches this
    physical element (the capture engine dedups by xpath + neighborhood
    signature). `primary_locator` is the strategy the replayer tries first;
    `fallback_locators` are tried in order on failure before invoking the
    healer.
    """
    id: str
    primary_locator: dict
    fallback_locators: list[dict]
    attributes: dict
    page_context: dict

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ElementFingerprint:
        return cls(
            id=d["id"],
            primary_locator=dict(d["primary_locator"]),
            fallback_locators=[dict(x) for x in d.get("fallback_locators", [])],
            attributes=dict(d.get("attributes", {})),
            page_context=dict(d.get("page_context", {})),
        )
